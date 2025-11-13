from __future__ import annotations

import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from core.downloader import (
    download_video,
    probe_video,
    DownloadCancelled,
)


class TaskNotFound(Exception):
    pass


class InvalidTaskState(Exception):
    pass


class TooManyActiveDownloads(Exception):
    def __init__(self, max_concurrent: int) -> None:
        super().__init__("too_many_active_downloads")
        self.max_concurrent = max_concurrent


class FileNotReady(Exception):
    pass


class FileMissing(Exception):
    pass


TaskState = str  # queued | running | completed | failed | cancelled


@dataclass
class Task:
    id: str
    url: str
    requested_format: Optional[str]
    audio_only: bool
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    state: TaskState = "queued"
    error: Optional[str] = None
    file_path: Optional[str] = None
    # Progress
    progress_percent: float = 0.0
    bytes_downloaded: Optional[int] = None
    total_bytes: Optional[int] = None
    speed_bps: Optional[float] = None
    eta_s: Optional[float] = None
    elapsed_s: Optional[float] = None
    # Internals
    _future: Optional[Future] = None
    _cancel_event: Optional[threading.Event] = None
    _cancel_requested: bool = False
    _last_progress_update_ts: float = 0.0


class TaskManager:
    """
    In-memory менеджер задач загрузки.
    Ограничивает параллелизм, ведёт прогресс, поддерживает отмену и TTL-очистку.
    """

    def __init__(
        self,
        downloads_dir: str,
        max_concurrent_downloads: int = 2,
        progress_update_interval_ms: int = 500,
        cleanup_interval_min: int = 10,
        download_ttl_hours: int = 24,
        persist_downloads: bool = False,
        queue_strategy: str = "enqueue",  # 'enqueue' | 'reject'
    ) -> None:
        self.downloads_dir = os.path.abspath(downloads_dir or "downloads")
        os.makedirs(self.downloads_dir, exist_ok=True)

        self.max_concurrent = max(1, int(max_concurrent_downloads or 1))
        self.progress_update_interval_ms = max(50, int(progress_update_interval_ms or 500))
        self.cleanup_interval_min = max(1, int(cleanup_interval_min or 10))
        self.download_ttl_hours = max(1, int(download_ttl_hours or 24))
        self.persist_downloads = bool(persist_downloads)
        self.queue_strategy = queue_strategy if queue_strategy in ("enqueue", "reject") else "enqueue"

        self._tasks: Dict[str, Task] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=self.max_concurrent, thread_name_prefix="gvz-dl")
        self._semaphore = threading.Semaphore(self.max_concurrent)
        self._running_ids: set[str] = set()

        self._stop_cleanup = threading.Event()
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            name="gvz-cleanup",
            daemon=True,
        )
        self._cleanup_thread.start()

    # Public API
    def start_download(self, url: str, fmt: Optional[str] = None, audio_only: bool = False) -> str:
        task_id = str(uuid4())
        with self._lock:
            running_now = len(self._running_ids)
            if running_now >= self.max_concurrent and self.queue_strategy == "reject":
                raise TooManyActiveDownloads(self.max_concurrent)
            task = Task(
                id=task_id,
                url=url,
                requested_format=fmt,
                audio_only=audio_only,
                state="queued" if running_now >= self.max_concurrent else "queued",
            )
            self._tasks[task_id] = task

        # Submit wrapper that acquires semaphore before actual run
        future = self._executor.submit(self._run_task, task_id)
        with self._lock:
            task._future = future
        return task_id

    def get_task(self, task_id: str) -> Task:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise TaskNotFound(task_id)
            return task

    def list_tasks(self) -> list[Task]:
        with self._lock:
            return list(self._tasks.values())

    def cancel_task(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise TaskNotFound(task_id)
            if task.state not in ("queued", "running"):
                raise InvalidTaskState("cannot_cancel_in_current_state")
            task._cancel_requested = True
            if task._cancel_event is None:
                task._cancel_event = threading.Event()
            task._cancel_event.set()
            if task.state == "queued" and task._future is not None:
                # Попробуем отменить до запуска
                task._future.cancel()
                task.state = "cancelled"
                task.finished_at = datetime.utcnow()
                task.updated_at = task.finished_at

    def get_file_path(self, task_id: str) -> str:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise TaskNotFound(task_id)
            if task.state != "completed":
                raise FileNotReady("not_completed_yet")
            if not task.file_path or not os.path.isfile(task.file_path):
                raise FileMissing("file_missing")
            return task.file_path

    # Internal
    def _run_task(self, task_id: str) -> None:
        task = self.get_task(task_id)
        # Acquire concurrency slot
        acquired = False
        try:
            if task._cancel_requested:
                raise DownloadCancelled("cancelled_before_start")
            self._semaphore.acquire()
            acquired = True
            with self._lock:
                self._running_ids.add(task_id)
                task.state = "running"
                task.updated_at = datetime.utcnow()
                if task._cancel_event is None:
                    task._cancel_event = threading.Event()

            start_ts = time.time()

            def on_percent(pct: float) -> None:
                now = time.time()
                if (now - task._last_progress_update_ts) * 1000.0 < self.progress_update_interval_ms:
                    return
                task._last_progress_update_ts = now
                with self._lock:
                    task.progress_percent = float(max(0.0, min(100.0, pct)))
                    task.elapsed_s = now - start_ts
                    task.updated_at = datetime.utcnow()

            def on_info(info: Dict[str, Any]) -> None:
                now = time.time()
                if (now - task._last_progress_update_ts) * 1000.0 < self.progress_update_interval_ms:
                    return
                task._last_progress_update_ts = now
                speed = info.get("speed")
                downloaded = info.get("downloaded_bytes")
                total = info.get("total_bytes")
                eta = None
                if speed and speed > 0 and total and downloaded is not None:
                    remaining = max(0, int(total) - int(downloaded))
                    eta = remaining / float(speed)
                with self._lock:
                    task.speed_bps = float(speed) if isinstance(speed, (int, float)) else None
                    task.bytes_downloaded = int(downloaded) if isinstance(downloaded, int) else None
                    task.total_bytes = int(total) if isinstance(total, int) else None
                    task.eta_s = float(eta) if eta is not None else None
                    task.elapsed_s = now - start_ts
                    task.updated_at = datetime.utcnow()

            # For audio_only=True — формат из API может игнорироваться (решение на уровне API)
            result_path = download_video(
                url=task.url,
                output_path=self.downloads_dir,
                progress_callback=on_percent,
                progress_info_callback=on_info,
                cancel_event=task._cancel_event,
                format=task.requested_format if not task.audio_only else None,
                audio_only=task.audio_only,
            )
            with self._lock:
                task.file_path = result_path
                task.progress_percent = 100.0
                task.state = "completed"
                task.finished_at = datetime.utcnow()
                task.updated_at = task.finished_at
        except DownloadCancelled:
            with self._lock:
                task.state = "cancelled"
                task.finished_at = datetime.utcnow()
                task.updated_at = task.finished_at
        except Exception as e:
            with self._lock:
                task.state = "failed"
                task.error = str(e)
                task.finished_at = datetime.utcnow()
                task.updated_at = task.finished_at
        finally:
            if acquired:
                self._semaphore.release()
            with self._lock:
                self._running_ids.discard(task_id)

    def _cleanup_loop(self) -> None:
        while not self._stop_cleanup.is_set():
            try:
                self._cleanup_once()
            except Exception:
                pass
            self._stop_cleanup.wait(self.cleanup_interval_min * 60)

    def _cleanup_once(self) -> None:
        if self.persist_downloads:
            return
        cutoff = datetime.utcnow() - timedelta(hours=self.download_ttl_hours)
        to_delete: list[str] = []
        with self._lock:
            for task_id, task in list(self._tasks.items()):
                if task.finished_at and task.finished_at < cutoff:
                    # try delete file
                    if task.file_path and os.path.isfile(task.file_path):
                        try:
                            os.remove(task.file_path)
                        except Exception:
                            pass
                    to_delete.append(task_id)
            for tid in to_delete:
                self._tasks.pop(tid, None)

    # Utility to validate format via probe (used optionally by API)
    def validate_format_available(self, url: str, fmt: str, audio_only: bool = False) -> bool:
        """
        Проверяет, что format_id доступен. Для audio_only можно требовать отсутствие видеокодека.
        """
        info = probe_video(url)
        fmts = info.get("formats") or []
        for f in fmts:
            if str(f.get("format_id")) == fmt:
                if audio_only:
                    vcodec = f.get("vcodec")
                    return not vcodec or vcodec == "none"
                return True
        return False


