from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional, Dict, List

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
from sqlalchemy import desc

from api.api_service import (
    TaskManager,
    TaskNotFound,
    InvalidTaskState,
    TooManyActiveDownloads,
    FileNotReady,
    FileMissing,
)
from core.db import SessionLocal, Download, SystemLog
from core.downloader import probe_video, analyze_video
from core.logger import log_event
from core.parser import find_media_urls, fetch_media_urls_with_browser
from core.site_parsers import get_adapter_for_url
from core.auth import (
    authenticate_user, register_user, update_user, delete_user,
    get_user_by_id, get_user_by_telegram_id, list_users as list_all_users,
    user_is_root,
)


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
    source: Optional[str] = None             # Источник запроса: "cli" | "ui" | "bot" | "api"
    user_id: Optional[int] = None            # ID пользователя (для аудита в system_logs)


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


class StatsResponse(BaseModel):
    """Агрегированная статистика по всем задачам для Dashboard."""
    total: int
    completed: int
    errors: int
    cancelled: int
    active: int           # queued + downloading
    total_bytes: int      # сумма file_size всех completed задач
    avg_file_size: float  # средний размер файла completed задач

    # Топ доменов: [{"domain": "youtube.com", "count": 42}, ...]
    top_domains: list[dict]

    # По источнику: {"cli": 10, "ui": 5, "bot": 3, "api": 1, "unknown": 2}
    by_source: dict

    # По пользователям: [{"user_id": 1, "count": 15}, ...]
    by_user: list[dict]

    # График по дням: [{"date": "2025-01-01", "count": 5, "bytes": 104857600}, ...]
    # За последние 90 дней
    daily: list[dict]


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    user_id: int
    name: str
    role: str


class RegisterRequest(BaseModel):
    email: str
    name: str
    password: str
    phone: Optional[str] = None


class RegisterResponse(BaseModel):
    id: int


class LogCreateRequest(BaseModel):
    """Тело запроса для POST /logs. Используется Bot и CLI-api-клиентом."""

    level: str
    source: str
    event_type: str
    message: str
    user_id: Optional[int] = None
    task_id: Optional[int] = None
    details: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None


class MediaResponse(BaseModel):
    hls_urls: list[str]
    file_urls: list[str]
    used_browser: bool
    static_found: bool
    page_title: str | None = None
    translations: List[Dict[str, str]] | None = None
    hls_streams: list[Dict[str, str]] = []
    adapter_name: str | None = None


class UserCreateRequest(BaseModel):
    """Тело запроса для POST /users — создание пользователя (только root)."""

    name: str
    email: str
    password: str
    role: str = "user"                         # допустимые значения: "admin", "user"
    telegram_chat_id: Optional[str] = None


class UserUpdateRequest(BaseModel):
    """Тело запроса для PUT /users/{id} — обновление пользователя (только root)."""

    name: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    telegram_chat_id: Optional[str] = None


class UserMeUpdateRequest(BaseModel):
    """Тело запроса для PUT /users/me — редактирование собственного профиля.

    Менять role нельзя — только через root PUT /users/{id}.
    Email можно изменить, если новый адрес ещё не занят.
    """

    name: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    telegram_chat_id: Optional[str] = None


class UserResponse(BaseModel):
    """Публичные данные пользователя (без пароля)."""

    id: int
    name: str
    email: str
    role: str
    is_active: bool
    telegram_chat_id: Optional[str] = None
    created_at: Optional[str] = None


class UserCreateResponse(BaseModel):
    """Ответ на успешное создание пользователя."""

    id: int
    name: str
    email: str
    role: str


# Типы событий, доступные пользователям с ролью admin в GET /logs
_ADMIN_ALLOWED_EVENT_TYPES = frozenset({
    "download_started", "download_completed", "download_failed", "download_cancelled",
    "convert_started", "convert_completed", "convert_failed",
})


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


@app.post("/auth/login", response_model=LoginResponse)
async def auth_login(req: LoginRequest, request: Request):
    """Аутентификация по email/имени и паролю. Возвращает user_id, name, role."""
    client_ip = request.client.host if request.client else None
    session = SessionLocal()
    try:
        user = await run_in_threadpool(authenticate_user, session, req.email, req.password)
        log_event(
            session, "INFO", "api", "login",
            f"Успешный вход: {req.email}",
            user_id=user.id,
            ip_address=client_ip,
        )
        return LoginResponse(user_id=user.id, name=user.name, role=user.role)
    except ValueError as e:
        log_event(
            session, "WARNING", "api", "login_failed",
            f"Неудачная попытка входа: {req.email}",
            details={"error": str(e)},
            ip_address=client_ip,
        )
        raise HTTPException(status_code=401, detail=str(e))
    finally:
        session.close()


@app.post("/auth/register", response_model=RegisterResponse, status_code=201)
async def auth_register(req: RegisterRequest):
    """Регистрация нового пользователя с ролью 'user'."""
    session = SessionLocal()
    try:
        user = await run_in_threadpool(
            register_user, session, req.email, req.name, req.password, req.phone
        )
        return RegisterResponse(id=user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        session.close()


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


@app.get("/stats", response_model=StatsResponse)
async def get_stats(
    scope: Optional[str] = Query(None, description="'my' — только задачи текущего пользователя"),
    caller_user_id: Optional[int] = Query(None),
    caller_role: Optional[str] = Query(None),
) -> StatsResponse:
    """Агрегированная статистика по задачам для Dashboard.

    Считает данные напрямую из таблицы downloads через Python-агрегации.
    Если scope='my' или роль user — возвращает статистику только по своим задачам.
    """
    from urllib.parse import urlparse
    from collections import defaultdict
    from datetime import datetime, timedelta

    session = SessionLocal()
    try:
        query = session.query(Download)
        # Фильтр по пользователю: scope="my" или роль user
        if (scope == "my" or caller_role == "user") and caller_user_id is not None:
            query = query.filter(Download.user_id == caller_user_id)
        all_tasks = query.all()

        total = len(all_tasks)
        completed = sum(1 for t in all_tasks if t.status == "completed")
        errors = sum(1 for t in all_tasks if t.status == "error")
        cancelled = sum(1 for t in all_tasks if t.status == "cancelled")
        active = sum(1 for t in all_tasks if t.status in ("queued", "downloading"))

        # Размеры файлов (только completed)
        completed_tasks = [t for t in all_tasks if t.status == "completed"]
        total_bytes = sum((t.file_size or 0) for t in completed_tasks)
        avg_file_size = (total_bytes / len(completed_tasks)) if completed_tasks else 0.0

        # Топ доменов
        domain_counts: dict[str, int] = defaultdict(int)
        for t in all_tasks:
            try:
                domain = urlparse(t.url).netloc or "unknown"
                # Убираем www. для единообразия
                domain = domain.lstrip("www.")
            except Exception:
                domain = "unknown"
            domain_counts[domain] += 1
        top_domains = sorted(
            [{"domain": k, "count": v} for k, v in domain_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:10]

        # По источнику
        source_counts: dict[str, int] = defaultdict(int)
        for t in all_tasks:
            source_counts[t.source or "unknown"] += 1
        by_source = dict(source_counts)

        # По пользователям (топ 20)
        user_counts: dict = defaultdict(int)
        for t in all_tasks:
            user_counts[t.user_id] += 1
        by_user = sorted(
            [{"user_id": k, "count": v} for k, v in user_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:20]

        # График по дням (последние 90 дней)
        daily_map: dict[str, dict] = {}
        cutoff = datetime.utcnow() - timedelta(days=90)
        for t in all_tasks:
            if t.created_at and t.created_at >= cutoff:
                day = t.created_at.strftime("%Y-%m-%d")
                if day not in daily_map:
                    daily_map[day] = {"date": day, "count": 0, "bytes": 0}
                daily_map[day]["count"] += 1
                if t.status == "completed":
                    daily_map[day]["bytes"] += (t.file_size or 0)
        daily = sorted(daily_map.values(), key=lambda x: x["date"])

        return StatsResponse(
            total=total,
            completed=completed,
            errors=errors,
            cancelled=cancelled,
            active=active,
            total_bytes=total_bytes,
            avg_file_size=avg_file_size,
            top_domains=top_domains,
            by_source=by_source,
            by_user=by_user,
            daily=daily,
        )
    finally:
        session.close()


@app.post("/downloads", response_model=StartDownloadResponse, status_code=201)
async def start_download(req: StartDownloadRequest, request: Request):
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
            source=req.source,
            user_id=req.user_id,
        )
        # Аудит: фиксируем создание задачи (task_id — UUID-строка, в details)
        _log_session = SessionLocal()
        try:
            log_event(
                _log_session, "INFO", "api", "download_started",
                f"Задача скачивания создана: {req.url}",
                user_id=req.user_id,
                details={"task_id": task_id, "url": str(req.url), "source": req.source},
                ip_address=request.client.host if request.client else None,
            )
        finally:
            _log_session.close()
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
async def list_downloads(
    caller_user_id: Optional[int] = Query(None),
    caller_role: Optional[str] = Query(None),
) -> list[TaskStatusResponse]:
    tasks = tm.list_tasks()
    # Пользователи видят только свои задачи; root и admin — все
    if caller_role == "user" and caller_user_id is not None:
        tasks = [t for t in tasks if t.get("user_id") == caller_user_id]
    return [_task_to_response(t) for t in tasks]


@app.get("/downloads/{task_id}")
async def get_download(task_id: str) -> TaskStatusResponse:
    task = tm.get_task(task_id)
    return _task_to_response(task)


@app.delete("/downloads/{task_id}", status_code=204)
async def cancel_download(
    task_id: str,
    caller_user_id: Optional[int] = Query(None),
    caller_role: Optional[str] = Query(None),
):
    # Пользователь может отменять только свои задачи
    if caller_role == "user":
        task = tm.get_task(task_id)  # TaskNotFound → 404 через exception handler
        if task.get("user_id") != caller_user_id:
            raise HTTPException(status_code=403, detail="Нет доступа к этой задаче")
    tm.cancel_task(task_id)
    _log_session = SessionLocal()
    try:
        log_event(
            _log_session, "INFO", "api", "download_cancelled",
            f"Задача {task_id} отменена",
            details={"task_id": task_id},
        )
    finally:
        _log_session.close()
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
        _log_session = SessionLocal()
        try:
            log_event(
                _log_session, "INFO", "api", "convert_started",
                f"Задача конвертации создана из {task_id}",
                details={
                    "source_task_id": task_id,
                    "convert_task_id": new_task_id,
                    "telegram_chat_id": telegram_chat_id,
                },
            )
        finally:
            _log_session.close()
        return StartDownloadResponse(id=new_task_id)
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="task_not_found")
    except FileMissing:
        raise HTTPException(status_code=404, detail="source_file_missing")
    except InvalidTaskState as e:
        raise HTTPException(status_code=409, detail=str(e))


# ---------------------------------------------------------------------------
# Системные логи
# ---------------------------------------------------------------------------

@app.post("/logs")
async def create_log(req: LogCreateRequest) -> dict:
    """Создаёт запись системного лога.

    Используется Bot и CLI-api-клиентом для централизованного сбора
    событий из всех слоёв приложения.
    """
    _log_session = SessionLocal()
    try:
        log_event(
            _log_session,
            level=req.level,
            source=req.source,
            event_type=req.event_type,
            message=req.message,
            user_id=req.user_id,
            task_id=req.task_id,
            details=req.details,
            ip_address=req.ip_address,
        )
    finally:
        _log_session.close()
    return {"ok": True}


@app.get("/logs")
async def get_logs(
    source: Optional[str] = Query(None, description="Фильтр по источнику (api/worker/bot/cli)"),
    level: Optional[str] = Query(None, description="Фильтр по уровню (INFO/WARNING/ERROR/CRITICAL)"),
    event_type: Optional[str] = Query(None, description="Фильтр по типу события"),
    user_id: Optional[int] = Query(None, description="Фильтр по user_id"),
    date_from: Optional[str] = Query(None, description="Начало периода (ISO 8601)"),
    date_to: Optional[str] = Query(None, description="Конец периода (ISO 8601)"),
    limit: int = Query(50, ge=1, le=200, description="Количество записей (макс. 200)"),
    offset: int = Query(0, ge=0, description="Смещение для пагинации"),
    caller_user_id: Optional[int] = Query(None, description="ID текущего пользователя (для фильтрации по роли)"),
    caller_role: Optional[str] = Query(None, description="Роль текущего пользователя: root/admin/user"),
) -> dict:
    """Список системных логов с фильтрацией и контролем доступа по роли.

    Права доступа:
    - root  → все записи без ограничений.
    - admin → только события скачивания и конвертации.
    - user  → только свои записи (WHERE user_id = caller_user_id).
    - без роли → все записи (внутренний/сервисный вызов).

    Возвращает: ``{"items": [...], "total": int, "limit": int, "offset": int}``
    """
    session = SessionLocal()
    try:
        query = session.query(SystemLog)

        # --- Контроль доступа по роли ------------------------------------
        if caller_role == "root":
            pass  # Без ограничений
        elif caller_role == "admin":
            query = query.filter(SystemLog.event_type.in_(_ADMIN_ALLOWED_EVENT_TYPES))
        elif caller_role == "user":
            if caller_user_id is None:
                return {"items": [], "total": 0, "limit": limit, "offset": offset}
            query = query.filter(SystemLog.user_id == caller_user_id)
        # else: роль не передана — сервисный вызов, без ограничений

        # --- Опциональные фильтры ----------------------------------------
        if source:
            query = query.filter(SystemLog.source == source)
        if level:
            query = query.filter(SystemLog.level == level.upper())
        if event_type:
            query = query.filter(SystemLog.event_type == event_type)
        if user_id is not None:
            query = query.filter(SystemLog.user_id == user_id)
        if date_from:
            try:
                dt_from = datetime.fromisoformat(date_from)
                query = query.filter(SystemLog.created_at >= dt_from)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Неверный формат date_from, ожидается ISO 8601 (например, 2025-01-01T00:00:00)",
                )
        if date_to:
            try:
                dt_to = datetime.fromisoformat(date_to)
                query = query.filter(SystemLog.created_at <= dt_to)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Неверный формат date_to, ожидается ISO 8601 (например, 2025-12-31T23:59:59)",
                )

        # --- Подсчёт и выборка -------------------------------------------
        total = query.count()
        items = (
            query.order_by(desc(SystemLog.created_at))
            .offset(offset)
            .limit(limit)
            .all()
        )

        return {
            "items": [
                {
                    "id": item.id,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                    "level": item.level,
                    "source": item.source,
                    "event_type": item.event_type,
                    "user_id": item.user_id,
                    "task_id": item.task_id,
                    "message": item.message,
                    "details": item.details,
                    "ip_address": item.ip_address,
                }
                for item in items
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Управление пользователями
# ---------------------------------------------------------------------------

@app.get("/users")
async def get_users_list(
    telegram_chat_id: Optional[str] = Query(None, description="Поиск по Telegram chat_id (для бота)"),
    caller_user_id: Optional[int] = Query(None),
    caller_role: Optional[str] = Query(None),
) -> Any:
    """Список пользователей (только root) или поиск по telegram_chat_id (для бота).

    - Если передан telegram_chat_id — возвращает одного пользователя или 404.
      Проверка роли не требуется (внутренний вызов от бота).
    - Иначе — требует caller_role == "root", возвращает всех пользователей.
      Поля: id, name, email, role, is_active, telegram_chat_id, created_at.
    """
    session = SessionLocal()
    try:
        if telegram_chat_id is not None:
            # Поиск по Telegram chat_id — используется ботом, без проверки роли
            user = get_user_by_telegram_id(session, telegram_chat_id)
            if user is None:
                raise HTTPException(status_code=404, detail="Пользователь не найден.")
            return UserResponse(
                id=user.id,
                name=user.name,
                email=user.email,
                role=user.role,
                is_active=user.is_active,
                telegram_chat_id=user.telegram_chat_id,
                created_at=user.created_at.isoformat() if user.created_at else None,
            )

        if caller_role != "root":
            raise HTTPException(status_code=403, detail="Доступ разрешён только root.")

        users = list_all_users(session)
        return [
            UserResponse(
                id=u.id,
                name=u.name,
                email=u.email,
                role=u.role,
                is_active=u.is_active,
                telegram_chat_id=u.telegram_chat_id,
                created_at=u.created_at.isoformat() if u.created_at else None,
            )
            for u in users
        ]
    finally:
        session.close()


@app.post("/users", response_model=UserCreateResponse, status_code=201)
async def create_user_endpoint(
    req: UserCreateRequest,
    caller_role: Optional[str] = Query(None),
) -> UserCreateResponse:
    """Создание нового пользователя (только root).

    Допустимые роли нового пользователя: "admin", "user".
    """
    if caller_role != "root":
        raise HTTPException(status_code=403, detail="Доступ разрешён только root.")

    if req.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Допустимые роли: admin, user.")

    session = SessionLocal()
    try:
        user = await run_in_threadpool(
            register_user,
            session,
            req.email,
            req.name,
            req.password,
            None,               # phone
            req.role,
            req.role == "admin",  # is_admin
            True,               # is_active
        )
        # Если при создании передан telegram_chat_id — сразу привязываем
        if req.telegram_chat_id:
            user = await run_in_threadpool(
                update_user,
                session,
                user.id,
                telegram_chat_id=req.telegram_chat_id,
            )
        return UserCreateResponse(id=user.id, name=user.name, email=user.email, role=user.role)
    except ValueError as e:
        msg = str(e)
        if "уже существует" in msg or "уже привязан" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    finally:
        session.close()


@app.get("/users/me", response_model=UserResponse)
async def get_me(
    caller_user_id: Optional[int] = Query(None),
) -> UserResponse:
    """Получить данные собственного профиля (любая авторизованная роль).

    Используется фронтендом для загрузки email и telegram_chat_id текущего пользователя.
    """
    if caller_user_id is None:
        raise HTTPException(status_code=401, detail="Необходима авторизация.")

    session = SessionLocal()
    try:
        user = get_user_by_id(session, caller_user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден.")
        return UserResponse(
            id=user.id,
            name=user.name,
            email=user.email,
            role=user.role,
            is_active=user.is_active,
            telegram_chat_id=user.telegram_chat_id,
            created_at=user.created_at.isoformat() if user.created_at else None,
        )
    finally:
        session.close()


# PUT /users/me должен быть определён ДО PUT /users/{user_id},
# чтобы FastAPI не пытался разобрать "me" как целочисленный user_id.
@app.put("/users/me", response_model=UserResponse)
async def update_me(
    req: UserMeUpdateRequest,
    caller_user_id: Optional[int] = Query(None),
) -> UserResponse:
    """Редактирование собственного профиля (любая авторизованная роль).

    Разрешено менять: name, email, password, telegram_chat_id.
    Role менять нельзя — только через root PUT /users/{id}.
    """
    if caller_user_id is None:
        raise HTTPException(status_code=401, detail="Необходима авторизация.")

    session = SessionLocal()
    try:
        user = await run_in_threadpool(
            update_user,
            session,
            caller_user_id,
            name=req.name,
            email=req.email,
            password=req.password,
            telegram_chat_id=req.telegram_chat_id,
        )
        return UserResponse(
            id=user.id,
            name=user.name,
            email=user.email,
            role=user.role,
            is_active=user.is_active,
            telegram_chat_id=user.telegram_chat_id,
            created_at=user.created_at.isoformat() if user.created_at else None,
        )
    except ValueError as e:
        msg = str(e)
        if "уже привязан" in msg or "уже существует" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    finally:
        session.close()


@app.put("/users/{user_id}", response_model=UserResponse)
async def update_user_endpoint(
    user_id: int,
    req: UserUpdateRequest,
    caller_user_id: Optional[int] = Query(None),
    caller_role: Optional[str] = Query(None),
) -> UserResponse:
    """Обновление любого пользователя (только root).

    Ограничения:
    - Нельзя изменить роль или деактивировать root-пользователя.
    - Нельзя редактировать самого себя — используйте PUT /users/me.
    """
    if caller_role != "root":
        raise HTTPException(status_code=403, detail="Доступ разрешён только root.")

    if caller_user_id is not None and caller_user_id == user_id:
        raise HTTPException(
            status_code=400,
            detail="Нельзя редактировать самого себя через этот эндпоинт. Используйте PUT /users/me.",
        )

    session = SessionLocal()
    try:
        # Проверяем, что цель не root, если меняем роль или статус активности
        target = get_user_by_id(session, user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден.")

        if user_is_root(target):
            if req.role is not None and req.role != "root":
                raise HTTPException(status_code=400, detail="Нельзя изменить роль root-пользователя.")
            if req.is_active is False:
                raise HTTPException(status_code=400, detail="Нельзя деактивировать root-пользователя.")

        user = await run_in_threadpool(
            update_user,
            session,
            user_id,
            name=req.name,
            email=req.email,
            password=req.password,
            role=req.role,
            is_active=req.is_active,
            telegram_chat_id=req.telegram_chat_id,
        )
        return UserResponse(
            id=user.id,
            name=user.name,
            email=user.email,
            role=user.role,
            is_active=user.is_active,
            telegram_chat_id=user.telegram_chat_id,
            created_at=user.created_at.isoformat() if user.created_at else None,
        )
    except HTTPException:
        raise
    except ValueError as e:
        msg = str(e)
        if "уже привязан" in msg or "уже существует" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    finally:
        session.close()


@app.delete("/users/{user_id}", status_code=204)
async def delete_user_endpoint(
    user_id: int,
    caller_user_id: Optional[int] = Query(None),
    caller_role: Optional[str] = Query(None),
) -> None:
    """Удаление пользователя (только root).

    Нельзя удалить:
    - пользователя с ролью root;
    - самого себя.
    """
    if caller_role != "root":
        raise HTTPException(status_code=403, detail="Доступ разрешён только root.")

    if caller_user_id is not None and caller_user_id == user_id:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя.")

    session = SessionLocal()
    try:
        target = get_user_by_id(session, user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден.")

        if user_is_root(target):
            raise HTTPException(status_code=400, detail="Нельзя удалить пользователя с ролью root.")

        await run_in_threadpool(delete_user, session, user_id)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        session.close()
