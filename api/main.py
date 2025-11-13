from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl

from api.service import (
    TaskManager,
    TaskNotFound,
    InvalidTaskState,
    TooManyActiveDownloads,
    FileNotReady,
    FileMissing,
)
from core.downloader import probe_video


# ENV config (прямо тут, по ТЗ)
DOWNLOADS_DIR = os.getenv("DOWNLOADS_DIR", "downloads")
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2"))
PROGRESS_UPDATE_INTERVAL_MS = int(os.getenv("PROGRESS_UPDATE_INTERVAL_MS", "500"))
CLEANUP_INTERVAL_MIN = int(os.getenv("CLEANUP_INTERVAL_MIN", "10"))
DOWNLOAD_TTL_HOURS = int(os.getenv("DOWNLOAD_TTL_HOURS", "24"))
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
    progress_update_interval_ms=PROGRESS_UPDATE_INTERVAL_MS,
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


class StartDownloadResponse(BaseModel):
    id: str


class TaskStatusResponse(BaseModel):
    id: str
    url: str
    state: str
    progress_percent: float
    bytes_downloaded: int | None = None
    total_bytes: int | None = None
    speed_bps: float | None = None
    eta_s: float | None = None
    elapsed_s: float | None = None
    filename: str | None = None
    error: str | None = None


def _task_to_response(task) -> TaskStatusResponse:
    return TaskStatusResponse(
        id=task.id,
        url=task.url,
        state=task.state,
        progress_percent=task.progress_percent,
        bytes_downloaded=task.bytes_downloaded,
        total_bytes=task.total_bytes,
        speed_bps=task.speed_bps,
        eta_s=task.eta_s,
        elapsed_s=task.elapsed_s,
        filename=os.path.basename(task.file_path) if task.file_path else None,
        error=task.error,
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
async def get_formats(url: HttpUrl = Query(...)) -> dict[str, Any]:
    try:
        info = probe_video(str(url))
        return info
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        # Ошибка извлечения — считаем как 422 для клиента
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/downloads", response_model=StartDownloadResponse, status_code=201)
async def start_download(req: StartDownloadRequest):
    # Валидация формата при необходимости
    fmt_to_use: str | None = req.format
    if req.audio_only:
        # По оговорке: игнорируем format, если audio_only=true
        fmt_to_use = None
    elif req.format:
        ok = False
        try:
            ok = tm.validate_format_available(str(req.url), req.format, audio_only=False)
        except Exception:
            ok = False
        if not ok:
            raise HTTPException(status_code=422, detail="format_unavailable")

    try:
        task_id = tm.start_download(str(req.url), fmt=fmt_to_use, audio_only=req.audio_only)
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
        raise HTTPException(status_code=500, detail="file_missing")
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="task_not_found")


