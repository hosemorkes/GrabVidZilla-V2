"""TaskManager — менеджер задач скачивания (только CRUD + TTL-очистка).

API больше не выполняет скачивания. Все задачи создаются со статусом ``queued``
и забираются Worker-ом через polling по общей SQLite БД.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

from core.db import SessionLocal, Download, SystemLog, init_db
from core.downloader import probe_video

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Исключения (публичный API, используются в api_main.py)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# TaskManager
# ---------------------------------------------------------------------------

class TaskManager:
    """Менеджер задач скачивания — только CRUD по БД и TTL-очистка.

    Скачивание выполняется отдельным Worker-процессом, который забирает задачи
    со статусом ``queued`` из общей SQLite БД.
    """

    def __init__(
        self,
        downloads_dir: str,
        max_concurrent_downloads: int = 2,
        cleanup_interval_min: int = 30,
        download_ttl_hours: int = 48,
        persist_downloads: bool = False,
        queue_strategy: str = "enqueue",
    ) -> None:
        self.downloads_dir = os.path.abspath(downloads_dir or "downloads")
        os.makedirs(self.downloads_dir, exist_ok=True)

        self.max_concurrent = max(1, int(max_concurrent_downloads or 1))
        self.cleanup_interval_min = max(1, int(cleanup_interval_min or 30))
        self.download_ttl_hours = max(1, int(download_ttl_hours or 48))
        self.persist_downloads = bool(persist_downloads)
        self.queue_strategy = (
            queue_strategy if queue_strategy in ("enqueue", "reject") else "enqueue"
        )

        # Инициализируем БД (создаёт таблицы, если их нет)
        init_db()

        # Фоновый поток TTL-очистки
        self._stop_cleanup = threading.Event()
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop, name="gvz-cleanup", daemon=True,
        )
        self._cleanup_thread.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_task(
        self,
        url: str,
        fmt: Optional[str] = None,
        audio_only: bool = False,
        cookies_path: Optional[str] = None,
        subtitle_lang: Optional[str] = None,
        webhook_url: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        source: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> str:
        """Создаёт задачу скачивания в БД со статусом ``queued``.

        Worker заберёт её при следующем polling-цикле.

        Args:
            url: URL для скачивания.
            fmt: Идентификатор формата (опционально).
            audio_only: Скачать только аудио.
            cookies_path: Путь к cookies.txt.
            subtitle_lang: Язык субтитров.
            webhook_url: URL для POST-уведомления при завершении.
            telegram_chat_id: chat_id инициатора в Telegram.
            source: Источник запроса («cli» | «ui» | «bot» | «api»).
            user_id: ID пользователя (FK на users.id, опционально).

        Returns:
            UUID задачи.
        """
        # Проверяем лимит активных задач (если стратегия reject)
        if self.queue_strategy == "reject":
            session = SessionLocal()
            try:
                active_count = (
                    session.query(Download)
                    .filter(Download.status.in_(["queued", "downloading"]))
                    .count()
                )
                if active_count >= self.max_concurrent:
                    raise TooManyActiveDownloads(self.max_concurrent)
            finally:
                session.close()

        task_id = str(uuid4())

        session = SessionLocal()
        try:
            dl = Download(
                id=task_id,
                url=url,
                status="queued",
                progress=0.0,
                format_id=fmt,
                audio_only=audio_only,
                cookies_path=cookies_path,
                subtitle_lang=subtitle_lang,
                webhook_url=webhook_url,
                telegram_chat_id=telegram_chat_id,
                source=source,
                user_id=user_id,
                created_at=datetime.utcnow(),
            )
            session.add(dl)
            session.commit()
        finally:
            session.close()

        return task_id

    def get_task(self, task_id: str) -> dict:
        """Возвращает задачу в виде словаря или бросает TaskNotFound."""
        session = SessionLocal()
        try:
            dl = session.query(Download).filter(Download.id == task_id).first()
            if not dl:
                raise TaskNotFound(task_id)
            return dl.to_dict()
        finally:
            session.close()

    def list_tasks(self) -> list[dict]:
        """Возвращает все задачи из БД (история + активные)."""
        session = SessionLocal()
        try:
            rows = (
                session.query(Download)
                .order_by(Download.created_at.desc())
                .all()
            )
            return [r.to_dict() for r in rows]
        finally:
            session.close()

    def cancel_task(self, task_id: str) -> None:
        """Отменяет или удаляет задачу.

        - ``queued`` — сразу ставим ``cancelled``.
        - ``downloading`` — ставим ``cancellation_requested=True``,
          Worker прочитает флаг в progress_callback и прервёт скачивание.
        - ``completed`` / ``error`` / ``cancelled`` — удаляем запись из БД
          (пользователь убирает из истории).
        """
        session = SessionLocal()
        try:
            dl = session.query(Download).filter(Download.id == task_id).first()
            if not dl:
                raise TaskNotFound(task_id)

            if dl.status in ("completed", "error", "cancelled"):
                # Завершённая задача — просто удаляем из истории
                session.delete(dl)
                session.commit()
                return

            now = datetime.utcnow()

            if dl.status == "queued":
                # Задача ещё не взята Worker-ом — отменяем напрямую
                dl.status = "cancelled"
                dl.error_type = "cancelled"
                dl.error_message = "Задача отменена пользователем"
                dl.finished_at = now
                dl.ttl_expires_at = now + timedelta(hours=self.download_ttl_hours)
            else:
                # Задача выполняется Worker-ом — ставим флаг
                dl.cancellation_requested = True

            session.commit()
        finally:
            session.close()

    def create_convert_task(
        self,
        task_id: str,
        telegram_chat_id: Optional[str] = None,
    ) -> str:
        """Создаёт задачу конвертации уже скачанного файла в MP4.

        Находит завершённую задачу, проверяет наличие файла на диске
        и создаёт новую задачу с флагом ``convert_to_mp4=True``.

        Args:
            task_id: ID завершённой (completed) задачи скачивания.
            telegram_chat_id: chat_id пользователя Telegram (опционально).

        Returns:
            UUID новой задачи конвертации.

        Raises:
            TaskNotFound: если исходная задача не найдена.
            InvalidTaskState: если задача не completed или файл уже в MP4.
            FileMissing: если файл не найден на диске.
        """
        session = SessionLocal()
        try:
            dl = session.query(Download).filter(Download.id == task_id).first()
            if not dl:
                raise TaskNotFound(task_id)
            if dl.status != "completed":
                raise InvalidTaskState("source_task_not_completed")
            if not dl.output_path or not os.path.isfile(dl.output_path):
                raise FileMissing("source_file_missing")
            ext = os.path.splitext(dl.output_path)[1].lower()
            if ext == ".mp4":
                raise InvalidTaskState("already_mp4")

            # Сохраняем нужные данные перед закрытием сессии
            source_path = dl.output_path
            source_url = dl.url
        finally:
            session.close()

        new_task_id = str(uuid4())
        now = datetime.utcnow()

        session = SessionLocal()
        try:
            new_dl = Download(
                id=new_task_id,
                url=source_url,         # URL исходного видео (для отображения)
                status="queued",
                progress=0.0,
                convert_to_mp4=True,    # флаг: Worker вызовет convert_to_mp4() вместо download_video()
                output_path=source_path,  # путь к исходному файлу; после конвертации Worker обновит
                telegram_chat_id=telegram_chat_id,
                created_at=now,
            )
            session.add(new_dl)
            session.commit()
        finally:
            session.close()

        return new_task_id

    def get_file_path(self, task_id: str) -> str:
        """Возвращает путь к скачанному файлу или бросает исключение."""
        session = SessionLocal()
        try:
            dl = session.query(Download).filter(Download.id == task_id).first()
            if not dl:
                raise TaskNotFound(task_id)
            if dl.status != "completed":
                raise FileNotReady("not_completed_yet")
            if not dl.output_path or not os.path.isfile(dl.output_path):
                raise FileMissing("file_missing")
            return dl.output_path
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Валидация формата (используется в api_main.py)
    # ------------------------------------------------------------------

    def validate_format_available(
        self, url: str, fmt: str, audio_only: bool = False,
    ) -> bool:
        """Проверяет, что format_id доступен для данного URL."""
        info = probe_video(url)
        fmts = info.get("formats") or []
        for f in fmts:
            if str(f.get("format_id")) == fmt:
                if audio_only:
                    vcodec = f.get("vcodec")
                    return not vcodec or vcodec == "none"
                return True
        return False

    # ------------------------------------------------------------------
    # Internal: TTL-очистка
    # ------------------------------------------------------------------

    def _cleanup_loop(self) -> None:
        """Фоновый цикл: удаляет просроченные записи и файлы."""
        while not self._stop_cleanup.is_set():
            try:
                self._cleanup_once()
            except Exception:
                logger.exception("Ошибка в цикле очистки")
            self._stop_cleanup.wait(self.cleanup_interval_min * 60)

    def _cleanup_once(self) -> None:
        """Одна итерация TTL-очистки задач и системных логов."""
        if self.persist_downloads:
            return

        now = datetime.utcnow()
        session = SessionLocal()
        try:
            expired = (
                session.query(Download)
                .filter(
                    Download.ttl_expires_at.isnot(None),
                    Download.ttl_expires_at < now,
                )
                .all()
            )
            for dl in expired:
                # Удаляем файл с диска
                if dl.output_path and os.path.isfile(dl.output_path):
                    try:
                        os.remove(dl.output_path)
                        logger.info("Удалён файл: %s", dl.output_path)
                    except Exception:
                        logger.warning("Не удалось удалить файл: %s", dl.output_path)

                session.delete(dl)
                logger.info("Удалена запись задачи: %s", dl.id)

            session.commit()
        finally:
            session.close()

        # Очистка старых системных логов (независимо от persist_downloads)
        self.cleanup_old_logs()

    def cleanup_old_logs(self) -> dict:
        """Удаляет устаревшие записи из таблицы system_logs.

        Две независимые стадии очистки:

        1. **По возрасту** (если ``LOG_RETENTION_DAYS > 0``): удаляет все
           записи, у которых ``created_at < now - LOG_RETENTION_DAYS``.
        2. **По лимиту строк** (всегда): если количество строк в таблице
           превышает ``MAX_LOG_ROWS``, удаляет самые старые записи (ORDER BY
           ``created_at ASC``) так, чтобы осталось не более ``MAX_LOG_ROWS``.

        Использует стандартный ``logging``, а не ``log_event``, чтобы избежать
        рекурсии (``log_event`` пишет в ту же таблицу).

        ENV-переменные:
            LOG_RETENTION_DAYS: Хранить логи N дней (0 = без ограничения по возрасту).
            MAX_LOG_ROWS: Максимальное число строк в system_logs (0 = без лимита).

        Returns:
            Словарь ``{"deleted_by_age": int, "deleted_by_limit": int}``.
        """
        log_retention_days = int(os.getenv("LOG_RETENTION_DAYS", "0"))
        max_log_rows = int(os.getenv("MAX_LOG_ROWS", "100000"))

        deleted_by_age = 0
        deleted_by_limit = 0

        session = SessionLocal()
        try:
            # --- Стадия 1: удаление по возрасту ----------------------------------
            if log_retention_days > 0:
                cutoff = datetime.utcnow() - timedelta(days=log_retention_days)
                deleted_by_age = (
                    session.query(SystemLog)
                    .filter(SystemLog.created_at < cutoff)
                    .delete(synchronize_session=False)
                )
                session.commit()
                if deleted_by_age:
                    logger.info(
                        "cleanup_old_logs: удалено по возрасту %d записей "
                        "(retention=%d дн., cutoff=%s)",
                        deleted_by_age, log_retention_days, cutoff.isoformat(),
                    )

            # --- Стадия 2: обрезка по лимиту строк --------------------------------
            if max_log_rows > 0:
                total_rows = session.query(SystemLog).count()
                excess = total_rows - max_log_rows
                if excess > 0:
                    # Получаем ID самых старых записей для удаления
                    oldest_ids = [
                        row.id
                        for row in (
                            session.query(SystemLog.id)
                            .order_by(SystemLog.created_at.asc())
                            .limit(excess)
                            .all()
                        )
                    ]
                    deleted_by_limit = (
                        session.query(SystemLog)
                        .filter(SystemLog.id.in_(oldest_ids))
                        .delete(synchronize_session=False)
                    )
                    session.commit()
                    logger.info(
                        "cleanup_old_logs: удалено по лимиту %d записей "
                        "(max=%d, было=%d)",
                        deleted_by_limit, max_log_rows, total_rows,
                    )

        except Exception:
            logger.exception("cleanup_old_logs: ошибка при очистке логов")
        finally:
            session.close()

        return {"deleted_by_age": deleted_by_age, "deleted_by_limit": deleted_by_limit}
