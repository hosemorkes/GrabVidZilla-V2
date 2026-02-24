"""GrabVidZilla Worker — самостоятельный процесс скачивания.

Worker периодически опрашивает SQLite-базу на наличие задач со статусом
``queued``, забирает их и выполняет скачивание через ``core.downloader``.
Прогресс, скорость и ETA записываются обратно в БД.

Запуск: ``python -m worker.worker_main``
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
import traceback
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Set

import httpx

from core.db import SessionLocal, Download, init_db
from core.downloader import (
    download_video,
    DownloadCancelled,
    compute_file_checksum,
    convert_to_mp4 as convert_video_to_mp4,
)
from core.errors import classify_download_error

# ---------------------------------------------------------------------------
# Конфигурация через переменные окружения
# ---------------------------------------------------------------------------

POLL_INTERVAL: int = int(os.getenv("WORKER_POLL_INTERVAL_SEC", "3"))
MAX_CONCURRENT: int = max(1, int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2")))
PROGRESS_UPDATE_INTERVAL_MS: int = int(os.getenv("PROGRESS_UPDATE_INTERVAL_MS", "1000"))
SHUTDOWN_TIMEOUT: int = int(os.getenv("WORKER_SHUTDOWN_TIMEOUT_SEC", "30"))
DOWNLOADS_DIR: str = os.getenv("DOWNLOADS_DIR", "Downloads")
DOWNLOAD_TTL_HOURS: int = int(os.getenv("DOWNLOAD_TTL_HOURS", "48"))

logger = logging.getLogger("grabvidzilla.worker")


class Worker:
    """Менеджер фоновых скачиваний с polling-циклом по SQLite."""

    def __init__(self) -> None:
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._shutting_down: bool = False

    # ------------------------------------------------------------------
    # Точка входа
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Главная точка входа Worker-а."""
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        init_db()

        logger.info(
            "Worker запущен: poll=%ds, max_concurrent=%d, downloads_dir=%s",
            POLL_INTERVAL, MAX_CONCURRENT, DOWNLOADS_DIR,
        )

        # Помечаем зависшие задачи от предыдущего запуска
        self._mark_interrupted_on_startup()

        # Устанавливаем обработчики сигналов для graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self._shutdown(s)))

        # Основной цикл опроса
        await self._poll_loop()

    async def _poll_loop(self) -> None:
        """Цикл опроса БД на наличие новых задач."""
        while not self._shutting_down:
            try:
                self._pick_tasks()
            except Exception:
                logger.exception("Ошибка в цикле опроса")

            await asyncio.sleep(POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Выбор задач из очереди
    # ------------------------------------------------------------------

    def _pick_tasks(self) -> None:
        """Забирает из БД задачи ``queued`` и запускает их как asyncio.Task.

        Использует атомарный UPDATE для защиты от конкурентного захвата
        одной задачи несколькими Worker-ами.
        """
        free_slots = MAX_CONCURRENT - len(self._running_tasks)
        if free_slots <= 0:
            return

        for _ in range(free_slots):
            claimed = _try_claim_task()
            if claimed is None:
                break  # нет свободных задач

            logger.info("Забрана задача %s (%s)", claimed, "<url>")
            self._running_tasks[claimed] = asyncio.create_task(
                self._run_download(claimed)
            )

    # ------------------------------------------------------------------
    # Скачивание одной задачи
    # ------------------------------------------------------------------

    async def _run_download(self, task_id: str) -> None:
        """Выполняет скачивание файла и обновляет запись в БД."""
        try:
            # Читаем параметры задачи
            session = SessionLocal()
            try:
                dl = session.query(Download).filter(Download.id == task_id).first()
                if not dl or dl.status == "cancelled":
                    return
                task_url = dl.url
                task_format = dl.format_id
                task_audio_only = dl.audio_only
                task_cookies = dl.cookies_path
                task_subtitle = dl.subtitle_lang
                # Флаги задачи конвертации
                task_convert_to_mp4 = dl.convert_to_mp4
                task_source_path = dl.output_path  # путь к исходному файлу (для конвертации)
            finally:
                session.close()

            # Таймер для троттлинга обновлений прогресса в БД
            last_progress_update = 0.0
            progress_interval_sec = PROGRESS_UPDATE_INTERVAL_MS / 1000.0

            def on_percent(pct: float) -> None:
                """Колбек процента — проверяет отмену и пишет прогресс в БД."""
                nonlocal last_progress_update
                pct = float(max(0.0, min(100.0, pct)))

                # Проверяем запрос на отмену
                _check_cancellation(task_id)

                now_ts = time.time()
                if now_ts - last_progress_update < progress_interval_sec:
                    return
                last_progress_update = now_ts

                s = SessionLocal()
                try:
                    d = s.query(Download).filter(Download.id == task_id).first()
                    if d:
                        d.progress = pct
                        s.commit()
                finally:
                    s.close()

            def on_info(info: Dict[str, Any]) -> None:
                """Колбек подробного прогресса — пишет speed/eta/file_size в БД."""
                nonlocal last_progress_update

                # Проверяем запрос на отмену
                _check_cancellation(task_id)

                now_ts = time.time()
                if now_ts - last_progress_update < progress_interval_sec:
                    return
                last_progress_update = now_ts

                speed = info.get("speed")
                downloaded = info.get("downloaded_bytes")
                total = info.get("total_bytes")

                percent: Optional[float] = None
                eta_val: Optional[float] = None
                if total and total > 0 and downloaded is not None and downloaded >= 0:
                    percent = float(downloaded) / float(total) * 100.0
                    percent = max(0.0, min(100.0, percent))
                if speed and speed > 0 and total and downloaded is not None:
                    remaining = max(0, int(total) - int(downloaded))
                    eta_val = remaining / float(speed)

                # Форматируем speed
                speed_str: Optional[str] = None
                if isinstance(speed, (int, float)) and speed > 0:
                    if speed >= 1024 * 1024:
                        speed_str = f"{speed / (1024 * 1024):.1f} MiB/s"
                    elif speed >= 1024:
                        speed_str = f"{speed / 1024:.1f} KiB/s"
                    else:
                        speed_str = f"{speed:.0f} B/s"

                # Форматируем eta
                eta_str: Optional[str] = None
                if eta_val is not None and eta_val >= 0:
                    m, sec = divmod(int(eta_val), 60)
                    h, m = divmod(m, 60)
                    eta_str = f"{h:02d}:{m:02d}:{sec:02d}"

                s = SessionLocal()
                try:
                    d = s.query(Download).filter(Download.id == task_id).first()
                    if d:
                        if percent is not None:
                            d.progress = percent
                        d.speed = speed_str
                        d.eta = eta_str
                        if isinstance(total, int) and total > 0:
                            d.file_size = total
                        s.commit()
                finally:
                    s.close()

            # Выполнение задачи (блокирующая операция — запускаем в потоке)
            loop = asyncio.get_running_loop()

            if task_convert_to_mp4:
                # Задача конвертации: конвертируем уже скачанный файл в MP4
                if not task_source_path or not os.path.isfile(task_source_path):
                    raise RuntimeError(f"Исходный файл для конвертации не найден: {task_source_path}")

                result_path: str = await loop.run_in_executor(
                    None,
                    lambda: convert_video_to_mp4(
                        input_path=task_source_path,
                        progress_callback=on_percent,
                    ),
                )
            else:
                # Обычная задача скачивания
                result_path = await loop.run_in_executor(
                    None,
                    lambda: download_video(
                        url=task_url,
                        output_path=DOWNLOADS_DIR,
                        progress_callback=on_percent,
                        progress_info_callback=on_info,
                        format=task_format if not task_audio_only else None,
                        audio_only=task_audio_only,
                        cookies_path=task_cookies,
                        subtitle_lang=task_subtitle,
                    ),
                )

            # Контрольная сумма
            checksum: Optional[str] = None
            try:
                checksum = compute_file_checksum(result_path)
            except Exception:
                checksum = None

            file_size: Optional[int] = None
            try:
                file_size = os.path.getsize(result_path)
            except Exception:
                pass

            # Обновляем запись — completed
            now = datetime.utcnow()
            session = SessionLocal()
            try:
                dl = session.query(Download).filter(Download.id == task_id).first()
                if dl:
                    dl.status = "completed"
                    dl.progress = 100.0
                    dl.output_path = result_path
                    dl.filename = os.path.basename(result_path)
                    dl.sha256 = checksum
                    dl.speed = None
                    dl.eta = None
                    if file_size:
                        dl.file_size = file_size
                    dl.finished_at = now
                    dl.ttl_expires_at = now + timedelta(hours=DOWNLOAD_TTL_HOURS)

                    # Проверяем корректность файла
                    if not checksum or (file_size is not None and file_size == 0):
                        dl.error_type = "corrupted_download"
                        dl.error_message = "Файл скачан, но повреждён или пуст"

                    session.commit()
                    logger.info("Задача %s завершена: %s", task_id, dl.filename)
            finally:
                session.close()

            # Уведомления (webhook + Telegram) — после commit
            await _send_notifications(task_id)

        except DownloadCancelled:
            self._mark_cancelled(task_id)

        except Exception as e:
            error_type, error_message = classify_download_error(e)
            error_details = traceback.format_exc()
            now = datetime.utcnow()
            session = SessionLocal()
            try:
                dl = session.query(Download).filter(Download.id == task_id).first()
                if dl:
                    dl.status = "error"
                    dl.error_type = error_type
                    dl.error_message = error_message
                    dl.error_details = error_details
                    dl.speed = None
                    dl.eta = None
                    dl.finished_at = now
                    dl.ttl_expires_at = now + timedelta(hours=DOWNLOAD_TTL_HOURS)
                    session.commit()
            finally:
                session.close()
            logger.error("Ошибка скачивания задачи %s: %s", task_id, e)

            # Уведомления при ошибке тоже отправляем
            await _send_notifications(task_id)

        finally:
            self._running_tasks.pop(task_id, None)

    # ------------------------------------------------------------------
    # Отмена задачи
    # ------------------------------------------------------------------

    def _mark_cancelled(self, task_id: str) -> None:
        """Помечает задачу как отменённую в БД."""
        now = datetime.utcnow()
        session = SessionLocal()
        try:
            dl = session.query(Download).filter(Download.id == task_id).first()
            if dl and dl.status != "cancelled":
                dl.status = "cancelled"
                dl.error_type = "cancelled"
                dl.error_message = "Задача отменена пользователем"
                dl.finished_at = now
                dl.ttl_expires_at = now + timedelta(hours=DOWNLOAD_TTL_HOURS)
                session.commit()
            logger.info("Задача %s отменена", task_id)
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Зависшие задачи при старте
    # ------------------------------------------------------------------

    def _mark_interrupted_on_startup(self) -> None:
        """При старте помечаем задачи ``downloading`` как error(interrupted).

        Задачи ``queued`` не трогаем — Worker заберёт их снова.
        """
        session = SessionLocal()
        try:
            stuck = (
                session.query(Download)
                .filter(Download.status == "downloading")
                .all()
            )
            now = datetime.utcnow()
            for dl in stuck:
                dl.status = "error"
                dl.error_type = "interrupted"
                dl.error_message = "Worker был перезапущен во время выполнения"
                dl.finished_at = now
                dl.ttl_expires_at = now + timedelta(hours=DOWNLOAD_TTL_HOURS)
                logger.info("Задача %s помечена как interrupted", dl.id)
            session.commit()
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Graceful shutdown
    # ------------------------------------------------------------------

    async def _shutdown(self, sig: signal.Signals) -> None:
        """Обработчик сигнала завершения: ждём текущие задачи или прерываем."""
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info("Получен сигнал %s, завершаем работу…", sig.name)

        if self._running_tasks:
            logger.info(
                "Ожидаем завершения %d задач (таймаут %d сек)…",
                len(self._running_tasks), SHUTDOWN_TIMEOUT,
            )
            # Даём текущим задачам время завершиться
            done, pending = await asyncio.wait(
                self._running_tasks.values(),
                timeout=SHUTDOWN_TIMEOUT,
            )

            # Задачи, которые не успели — помечаем interrupted
            for task in pending:
                task.cancel()
            if pending:
                # Ждём завершения отменённых задач
                await asyncio.wait(pending, timeout=5)

            # Помечаем все оставшиеся running задачи как interrupted
            session = SessionLocal()
            try:
                still_running = (
                    session.query(Download)
                    .filter(Download.status == "downloading")
                    .all()
                )
                now = datetime.utcnow()
                for dl in still_running:
                    dl.status = "error"
                    dl.error_type = "interrupted"
                    dl.error_message = "Worker остановлен во время выполнения"
                    dl.finished_at = now
                    dl.ttl_expires_at = now + timedelta(hours=DOWNLOAD_TTL_HOURS)
                    logger.info("Задача %s помечена как interrupted (shutdown)", dl.id)
                session.commit()
            finally:
                session.close()

        logger.info("Worker остановлен.")


def _try_claim_task() -> Optional[str]:
    """Атомарно захватывает одну queued-задачу через UPDATE с проверкой.

    Возвращает task_id или None, если свободных задач нет.
    Корректно работает и с SQLite (WAL + busy_timeout), и с PostgreSQL.
    """
    session = SessionLocal()
    try:
        dialect = session.bind.dialect.name  # 'sqlite' или 'postgresql'

        query = (
            session.query(Download)
            .filter(Download.status == "queued")
            .order_by(Download.created_at.asc())
        )

        if dialect == "postgresql":
            task = query.with_for_update(skip_locked=True).first()
        else:
            task = query.first()

        if task is None:
            return None

        # Атомарно сменить статус — только если он всё ещё queued
        updated = (
            session.query(Download)
            .filter(Download.id == task.id, Download.status == "queued")
            .update(
                {"status": "downloading", "started_at": datetime.utcnow()},
                synchronize_session=False,
            )
        )
        session.commit()

        return task.id if updated > 0 else None
    finally:
        session.close()


def _check_cancellation(task_id: str) -> None:
    """Проверяет флаг ``cancellation_requested`` в БД и бросает DownloadCancelled."""
    session = SessionLocal()
    try:
        dl = session.query(Download).filter(Download.id == task_id).first()
        if dl and dl.cancellation_requested:
            raise DownloadCancelled("Задача отменена пользователем")
    finally:
        session.close()


def _format_size(size_bytes: Optional[int]) -> str:
    """Форматирует размер файла в читаемую строку."""
    if not size_bytes or size_bytes <= 0:
        return "неизвестно"
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 ** 3):.1f} GB"
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 ** 2):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


async def _send_notifications(task_id: str) -> None:
    """Отправляет webhook и Telegram-уведомление для завершённой задачи."""
    session = SessionLocal()
    try:
        task = session.query(Download).filter(Download.id == task_id).first()
        if not task:
            return
        # Копируем нужные поля, чтобы не держать сессию
        webhook_url = task.webhook_url
        webhook_sent = task.webhook_sent
        telegram_chat_id = task.telegram_chat_id
        status = task.status
        url = task.url
        filename = task.filename
        file_size = task.file_size
        error_type = task.error_type
        error_message = task.error_message
        finished_at = task.finished_at
    finally:
        session.close()

    # Webhook
    if webhook_url and not webhook_sent:
        await _send_webhook(
            task_id, webhook_url, status, url, filename,
            file_size, error_type, error_message, finished_at,
        )

    # Telegram — только для задач НЕ от бота.
    # Если telegram_chat_id заполнен — задача создана через бота,
    # бот сам отслеживает прогресс через polling и покажет результат.
    if telegram_chat_id:
        return  # бот сам справится — не дублируем


async def _send_webhook(
    task_id: str,
    webhook_url: str,
    status: str,
    url: str,
    filename: Optional[str],
    file_size: Optional[int],
    error_type: Optional[str],
    error_message: Optional[str],
    finished_at: Optional[datetime],
) -> None:
    """Отправляет POST-уведомление на webhook_url."""
    payload = {
        "id": task_id,
        "status": status,
        "url": url,
        "filename": filename,
        "file_size": file_size,
        "error_type": error_type,
        "error_message": error_message,
        "finished_at": finished_at.isoformat() if finished_at else None,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=payload)
            resp.raise_for_status()

        # Помечаем как отправленный
        session = SessionLocal()
        try:
            session.query(Download).filter(Download.id == task_id).update(
                {"webhook_sent": True}
            )
            session.commit()
        finally:
            session.close()

        logger.info("Webhook отправлен: %s → %d", webhook_url, resp.status_code)
    except Exception as e:
        logger.warning("Webhook не доставлен (%s): %s", webhook_url, e)


async def _notify_telegram(
    task_id: str,
    chat_id: str,
    status: str,
    url: str,
    filename: Optional[str],
    file_size: Optional[int],
    error_message: Optional[str],
) -> None:
    """Отправляет уведомление инициатору задачи в Telegram через Bot API."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return

    api_url = os.getenv("GVZ_API_PUBLIC_URL") or os.getenv("GVZ_API_URL", "http://localhost:8000")

    if status == "completed":
        text = (
            f"✅ Скачивание завершено!\n"
            f"📄 {filename}\n"
            f"📦 {_format_size(file_size)}\n"
            f"🔗 {api_url}/downloads/{task_id}/file"
        )
    else:
        text = (
            f"❌ Ошибка скачивания\n"
            f"🔗 {url}\n"
            f"⚠️ {error_message or 'Неизвестная ошибка'}"
        )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
            )
        logger.info("Telegram уведомление отправлено → chat_id=%s", chat_id)
    except Exception as e:
        logger.warning("Telegram уведомление не доставлено: %s", e)


def main() -> None:
    """Точка входа при запуске ``python -m worker.worker_main``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    worker = Worker()

    # На Windows signal handlers через loop.add_signal_handler не работают,
    # поэтому используем простой try/except KeyboardInterrupt как fallback.
    if sys.platform == "win32":
        async def _run_windows() -> None:
            os.makedirs(DOWNLOADS_DIR, exist_ok=True)
            init_db()
            logger.info(
                "Worker запущен (Windows): poll=%ds, max_concurrent=%d, downloads_dir=%s",
                POLL_INTERVAL, MAX_CONCURRENT, DOWNLOADS_DIR,
            )
            worker._mark_interrupted_on_startup()
            await worker._poll_loop()

        try:
            asyncio.run(_run_windows())
        except KeyboardInterrupt:
            logger.info("Worker остановлен (KeyboardInterrupt).")
    else:
        asyncio.run(worker.run())


if __name__ == "__main__":
    main()
