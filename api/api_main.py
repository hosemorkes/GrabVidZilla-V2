from __future__ import annotations

import os
from typing import Any, Optional, Dict, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl

from api.api_service import (
    TaskManager,
    TaskNotFound,
    InvalidTaskState,
    TooManyActiveDownloads,
    FileNotReady,
    FileMissing,
)
from core.downloader import probe_video, analyze_video
from core.parser import find_media_urls, fetch_media_urls_with_browser
from core.site_parsers import get_adapter_for_url


# ENV config (прямо тут, по ТЗ)
DOWNLOADS_DIR = os.getenv("DOWNLOADS_DIR", "downloads")
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2"))
CLEANUP_INTERVAL_MIN = int(os.getenv("CLEANUP_INTERVAL_MIN", "30"))
DOWNLOAD_TTL_HOURS = int(os.getenv("DOWNLOAD_TTL_HOURS", "48"))
PERSIST_DOWNLOADS = os.getenv("PERSIST_DOWNLOADS", "false").lower() in ("1", "true", "yes")
QUEUE_STRATEGY = os.getenv("QUEUE_STRATEGY", "enqueue")

app = FastAPI(title="GrabVidZilla API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Инициализация менеджера задач
tm = TaskManager(
    downloads_dir=DOWNLOADS_DIR,
    max_concurrent_downloads=MAX_CONCURRENT_DOWNLOADS,
    cleanup_interval_min=CLEANUP_INTERVAL_MIN,
    download_ttl_hours=DOWNLOAD_TTL_HOURS,
    persist_downloads=PERSIST_DOWNLOADS,
    queue_strategy=QUEUE_STRATEGY,
)


# Schemas
class StartDownloadRequest(BaseModel):
    url: HttpUrl
    format: Optional[str] = None
    audio_only: bool = False
    cookies_path: Optional[str] = None
    subtitle_lang: Optional[str] = None
    webhook_url: Optional[str] = None        # URL для POST-уведомления при завершении
    telegram_chat_id: Optional[str] = None   # chat_id инициатора в Telegram (передаётся ботом)


class StartDownloadResponse(BaseModel):
    id: str


class TaskStatusResponse(BaseModel):
    id: str
    url: str
    status: str
    progress: float
    speed: str | None = None
    eta: str | None = None
    filename: str | None = None
    file_size: int | None = None
    sha256: str | None = None
    format_id: str | None = None
    audio_only: bool = False
    error_message: str | None = None
    error_type: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class MediaResponse(BaseModel):
    hls_urls: list[str]
    file_urls: list[str]
    used_browser: bool
    static_found: bool
    page_title: str | None = None
    translations: List[Dict[str, str]] | None = None
    hls_streams: list[Dict[str, str]] = []
    adapter_name: str | None = None


def _task_to_response(task: dict) -> TaskStatusResponse:
    """Преобразует словарь задачи из TaskManager в ответ API."""
    return TaskStatusResponse(
        id=task["id"],
        url=task["url"],
        status=task["status"],
        progress=task.get("progress", 0.0),
        speed=task.get("speed"),
        eta=task.get("eta"),
        filename=task.get("filename"),
        file_size=task.get("file_size"),
        sha256=task.get("sha256"),
        format_id=task.get("format_id"),
        audio_only=task.get("audio_only", False),
        error_message=task.get("error_message"),
        error_type=task.get("error_type"),
        created_at=task.get("created_at"),
        started_at=task.get("started_at"),
        finished_at=task.get("finished_at"),
    )


# Error mappers
@app.exception_handler(TaskNotFound)
async def _handle_not_found(_req, _exc: TaskNotFound):
    raise HTTPException(status_code=404, detail="task_not_found")


@app.exception_handler(InvalidTaskState)
async def _handle_invalid_state(_req, exc: InvalidTaskState):
    raise HTTPException(status_code=409, detail=str(exc))


@app.exception_handler(TooManyActiveDownloads)
async def _handle_too_many(_req, exc: TooManyActiveDownloads):
    raise HTTPException(
        status_code=429,
        detail={"error": "too_many_active_downloads", "max_concurrent": exc.max_concurrent},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/formats")
async def get_formats(
    url: HttpUrl = Query(...),
    cookies_path: Optional[str] = Query(None),
) -> dict[str, Any]:
    try:
        info = probe_video(str(url), cookies_path=cookies_path)
        return info
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        # Ошибка извлечения — считаем как 422 для клиента
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/analyze")
async def analyze(
    url: HttpUrl = Query(...),
    cookies_path: Optional[str] = Query(None),
) -> dict[str, Any]:
    """
    Анализирует видео и возвращает структурированные данные:
    - info: метаданные ролика
    - qualities: список качеств (например, ['2160p', '1080p', 'audio only'])
    - subtitle_langs: список языков субтитров (например, ['en', 'ru'])
    """
    try:
        info, qualities, subtitle_langs = analyze_video(str(url), cookies_path=cookies_path)
        return {
            "info": info,
            "qualities": qualities,
            "subtitle_langs": subtitle_langs,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/media", response_model=MediaResponse)
async def get_media_links(
    url: HttpUrl = Query(...),
    cookies_path: Optional[str] = Query(None),
    translation_hash: Optional[str] = Query(None),
    use_browser: bool = Query(False, description="Принудительно использовать браузерный парсер."),
    fallback_to_browser: bool = Query(
        True,
        description="Если статический парсер ничего не нашёл, попробовать браузер.",
    ),
    use_adapter: bool = Query(
        False,
        description="Использовать site-specific адаптер (автоопределение по URL).",
    ),
    proxy_server: Optional[str] = Query(
        None,
        description="Адрес прокси (например, http://host:3128 или socks5://host:1080).",
    ),
    proxy_username: Optional[str] = Query(None),
    proxy_password: Optional[str] = Query(None),
) -> MediaResponse:
    proxy: Optional[dict[str, str]] = None
    if proxy_server:
        proxy = {"server": proxy_server}
        if proxy_username:
            proxy["username"] = proxy_username
        if proxy_password:
            proxy["password"] = proxy_password

    hls_urls: list[str] = []
    file_urls: list[str] = []
    seen_hls: set[str] = set()
    seen_files: set[str] = set()
    page_title: Optional[str] = None
    translations: Optional[List[Dict[str, str]]] = None
    hls_streams: list[Dict[str, str]] = []
    adapter_name: Optional[str] = None

    def _extend_unique(target: list[str], seen: set[str], values: list[str]) -> None:
        for value in values:
            if value not in seen:
                seen.add(value)
                target.append(value)

    # Если запрошен site-specific адаптер — используем его вместо generic парсера
    if use_adapter:
        adapter = get_adapter_for_url(str(url))
        if not adapter:
            return MediaResponse(
                hls_urls=[],
                file_urls=[],
                used_browser=False,
                static_found=False,
                page_title=None,
                translations=None,
                hls_streams=[],
                adapter_name=None,
            )
        adapter_name = adapter.name
        try:
            a_hls, a_files, a_title, a_translations, a_streams = await run_in_threadpool(
                adapter.parse,
                str(url),
                cookies_path,
                translation_hash,
                proxy,
            )
            _extend_unique(hls_urls, seen_hls, a_hls)
            _extend_unique(file_urls, seen_files, a_files)
            if a_title:
                page_title = a_title
            if a_translations:
                translations = a_translations
            if a_streams:
                hls_streams.extend(a_streams)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=422, detail=str(e))

        unique_streams: list[Dict[str, str]] = []
        seen_stream_urls: set[str] = set()
        for stream in hls_streams:
            url_value = stream.get("url") if isinstance(stream, dict) else None
            if isinstance(url_value, str) and url_value:
                if url_value in seen_stream_urls:
                    continue
                seen_stream_urls.add(url_value)
                quality_value = stream.get("quality") if isinstance(stream, dict) else ""
                unique_streams.append({"url": url_value, "quality": quality_value or ""})

        return MediaResponse(
            hls_urls=hls_urls,
            file_urls=file_urls,
            used_browser=False,
            static_found=bool(hls_urls or file_urls),
            page_title=page_title,
            translations=translations,
            hls_streams=unique_streams,
            adapter_name=adapter_name,
        )

    # Generic парсинг (статический + браузерный)
    static_found = False
    static_error: Optional[RuntimeError] = None

    try:
        static_hls, static_files, static_title, static_translations, static_streams = await run_in_threadpool(
            find_media_urls,
            str(url),
            cookies_path,
            translation_hash,
        )
        static_found = bool(static_hls or static_files)
        _extend_unique(hls_urls, seen_hls, static_hls)
        _extend_unique(file_urls, seen_files, static_files)
        if static_title:
            page_title = static_title
        if static_translations:
            translations = static_translations
        if static_streams:
            hls_streams.extend(static_streams)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        static_error = e

    should_use_browser = False
    if use_browser:
        should_use_browser = True
    elif fallback_to_browser and (not static_found or static_error is not None):
        should_use_browser = True
    used_browser = False

    if should_use_browser:
        used_browser = True
        browser_hls, browser_files, browser_title, browser_translations, browser_streams = await run_in_threadpool(
            fetch_media_urls_with_browser,
            str(url),
            cookies_path,
            proxy,
            translation_hash,
        )
        _extend_unique(hls_urls, seen_hls, browser_hls)
        _extend_unique(file_urls, seen_files, browser_files)
        if browser_title:
            page_title = browser_title
        if browser_translations:
            translations = browser_translations
        if browser_streams:
            hls_streams.extend(browser_streams)

    if static_error and not used_browser:
        raise HTTPException(status_code=422, detail=str(static_error))

    unique_streams: list[Dict[str, str]] = []
    seen_stream_urls: set[str] = set()
    for stream in hls_streams:
        url_value = stream.get("url") if isinstance(stream, dict) else None
        if isinstance(url_value, str) and url_value:
            if url_value in seen_stream_urls:
                continue
            seen_stream_urls.add(url_value)
            quality_value = stream.get("quality") if isinstance(stream, dict) else ""
            unique_streams.append({"url": url_value, "quality": quality_value or ""})

    return MediaResponse(
        hls_urls=hls_urls,
        file_urls=file_urls,
        used_browser=used_browser,
        static_found=static_found,
        page_title=page_title,
        translations=translations,
        hls_streams=unique_streams,
    )


@app.post("/downloads", response_model=StartDownloadResponse, status_code=201)
async def start_download(req: StartDownloadRequest):
    # Валидация формата при необходимости
    fmt_to_use: str | None = req.format
    if req.audio_only:
        # По оговорке: игнорируем format, если audio_only=true
        fmt_to_use = None
    elif req.format:
        # Проверяем, является ли формат селектором (содержит *, +, [, ]) или конкретным format_id
        # Селекторы формата валидны по определению, их не нужно проверять
        is_selector = any(char in req.format for char in ["*", "+", "[", "]", "/"])
        if not is_selector:
            # Это конкретный format_id - проверяем его доступность
            ok = False
            try:
                ok = tm.validate_format_available(str(req.url), req.format, audio_only=False)
            except Exception:
                ok = False
            if not ok:
                raise HTTPException(status_code=422, detail="format_unavailable")

    try:
        task_id = tm.create_task(
            str(req.url),
            fmt=fmt_to_use,
            audio_only=req.audio_only,
            cookies_path=req.cookies_path,
            subtitle_lang=req.subtitle_lang,
            webhook_url=req.webhook_url,
            telegram_chat_id=req.telegram_chat_id,
        )
        return StartDownloadResponse(id=task_id)
    except TooManyActiveDownloads as e:
        # Если стратегия reject — отдаём 429
        raise HTTPException(
            status_code=429,
            detail={"error": "too_many_active_downloads", "max_concurrent": e.max_concurrent},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/downloads")
async def list_downloads() -> list[TaskStatusResponse]:
    tasks = tm.list_tasks()
    return [_task_to_response(t) for t in tasks]


@app.get("/downloads/{task_id}")
async def get_download(task_id: str) -> TaskStatusResponse:
    task = tm.get_task(task_id)
    return _task_to_response(task)


@app.delete("/downloads/{task_id}", status_code=204)
async def cancel_download(task_id: str):
    tm.cancel_task(task_id)
    return {"status": "cancelled"}


@app.get("/downloads/{task_id}/file")
async def get_downloaded_file(task_id: str):
    try:
        path = tm.get_file_path(task_id)
        return FileResponse(path, filename=os.path.basename(path))
    except FileNotReady:
        raise HTTPException(status_code=409, detail="file_not_ready")
    except FileMissing:
        raise HTTPException(status_code=404, detail="file_missing")
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="task_not_found")


@app.post("/downloads/{task_id}/convert", response_model=StartDownloadResponse, status_code=201)
async def convert_download(
    task_id: str,
    telegram_chat_id: Optional[str] = Query(None, description="chat_id Telegram для уведомлений"),
) -> StartDownloadResponse:
    """Создаёт задачу конвертации уже скачанного файла в MP4 (H.264 + AAC).

    Worker заберёт задачу при следующем polling-цикле и вызовет ffmpeg.
    Исходный файл удаляется после успешной конвертации.
    """
    try:
        new_task_id = tm.create_convert_task(task_id, telegram_chat_id=telegram_chat_id)
        return StartDownloadResponse(id=new_task_id)
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="task_not_found")
    except FileMissing:
        raise HTTPException(status_code=404, detail="source_file_missing")
    except InvalidTaskState as e:
        raise HTTPException(status_code=409, detail=str(e))

