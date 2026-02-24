"""GrabVidZilla Telegram Bot — клиент для скачивания видео через API.

Бот работает через HTTP API (не импортирует core напрямую).
Использует Long Polling — не требует публичного HTTPS-адреса.

Запуск: ``python -m bot.bot_main``

ENV:
    TELEGRAM_BOT_TOKEN       — токен от @BotFather
    TELEGRAM_ALLOWED_USERS   — список user_id через запятую: "123456789,987654321"
    GVZ_API_URL              — адрес API (по умолчанию http://localhost:8000)
"""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()  # читает .env автоматически при запуске

import asyncio
import logging
import os
import os.path
import sys
import time
import uuid
from datetime import datetime, timezone
from html import escape as html_escape
from typing import Optional

import httpx
from aiogram import Bot, Dispatcher, Router, BaseMiddleware, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)

# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USERS_RAW: str = os.getenv("TELEGRAM_ALLOWED_USERS", "")
API_URL: str = os.getenv("GVZ_API_URL", "http://localhost:8000")
# Публичный адрес API для ссылок пользователю (может отличаться от внутреннего Docker-адреса)
API_PUBLIC_URL: str = os.getenv("GVZ_API_PUBLIC_URL", API_URL)

# Лимит Telegram для отправки файлов через Bot API — 50 MB (обычный бот).
# Для Local Bot API Server лимит до 2 GB, ставим 500 MB как разумный максимум.
TG_FILE_LIMIT: int = 500_000_000  # 500 MB

logger = logging.getLogger("grabvidzilla.bot")

router = Router()

# Временное хранилище параметров скачивания для inline-кнопок.
# Ключ: 8-символьный UUID, значение: {"url", "fmt", "audio_only"}.
# Записи живут до перезапуска бота — пользователь выбирает формат сразу.
_pending_downloads: dict[str, dict] = {}


def _store_pending(url: str, fmt: str | None, audio_only: bool) -> str:
    """Сохраняет параметры скачивания, возвращает короткий ключ (8 символов)."""
    key = uuid.uuid4().hex[:8]
    _pending_downloads[key] = {"url": url, "fmt": fmt, "audio_only": audio_only}
    return key


def _pop_pending(key: str) -> dict | None:
    """Извлекает и удаляет параметры по ключу."""
    return _pending_downloads.pop(key, None)


# ---------------------------------------------------------------------------
# Whitelist middleware
# ---------------------------------------------------------------------------

def _parse_allowed_users() -> set[int]:
    """Парсит список разрешённых user_id из ENV."""
    ids: set[int] = set()
    for part in ALLOWED_USERS_RAW.split(","):
        part = part.strip()
        if part:
            try:
                ids.add(int(part))
            except ValueError:
                logger.warning("Некорректный user_id в TELEGRAM_ALLOWED_USERS: %s", part)
    return ids


class WhitelistMiddleware(BaseMiddleware):
    """Пропускает только пользователей из TELEGRAM_ALLOWED_USERS."""

    async def __call__(self, handler, event: Update, data: dict):
        allowed = _parse_allowed_users()
        if not allowed:
            # Если список пуст — пускаем всех (удобно для отладки)
            return await handler(event, data)

        user = data.get("event_from_user")
        if user and user.id not in allowed:
            if isinstance(event, Message):
                await event.answer("⛔ Доступ запрещён.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Доступ запрещён.", show_alert=True)
            return
        return await handler(event, data)


# ---------------------------------------------------------------------------
# HTTP-клиент к API
# ---------------------------------------------------------------------------

_http: Optional[httpx.AsyncClient] = None


def _get_http() -> httpx.AsyncClient:
    """Возвращает переиспользуемый httpx-клиент."""
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(base_url=API_URL, timeout=60)
    return _http


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _format_size(size_bytes: int | None) -> str:
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


def _download_link(task_id: str) -> str:
    """Формирует публичную ссылку для скачивания файла по task_id."""
    return f"{API_PUBLIC_URL}/downloads/{task_id}/file"


def _short_id(task_id: str) -> str:
    """Первые 8 символов UUID для удобного отображения."""
    return task_id[:8]


def _time_ago(iso_str: str | None) -> str:
    """Вычисляет 'X минут/часов назад' из ISO-строки."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        minutes = int(delta.total_seconds() / 60)
        if minutes < 60:
            return f"{minutes} мин назад"
        hours = minutes // 60
        if hours < 24:
            return f"{hours} ч назад"
        days = hours // 24
        return f"{days} дн назад"
    except Exception:
        return ""


async def _send_file_to_chat(bot: Bot, chat_id: int | str, task_id: str) -> bool:
    """Скачивает файл через API и отправляет в чат как документ.

    Возвращает True при успехе, False при ошибке.
    """
    try:
        # Получаем информацию о задаче (имя файла)
        info_resp = await _get_http().get(f"/downloads/{task_id}")
        info_resp.raise_for_status()
        task_info = info_resp.json()

        # Берём имя файла из поля filename (уже содержит расширение)
        filename = task_info.get("filename") or "video"

        # Страховка: если расширения нет — пробуем достать из output_path
        if "." not in filename:
            output_path = task_info.get("output_path") or ""
            if output_path:
                ext = os.path.splitext(output_path)[1]  # ".webm", ".mp4" и т.д.
                if ext:
                    filename = filename + ext

        # Скачиваем файл через внутренний API
        file_resp = await _get_http().get(
            f"/downloads/{task_id}/file", timeout=600,
        )
        file_resp.raise_for_status()

        # Content-Disposition как дополнительный источник имени
        cd = file_resp.headers.get("content-disposition", "")
        if "filename=" in cd:
            cd_filename = cd.split("filename=")[-1].strip('"').strip("'").strip()
            if cd_filename:
                filename = cd_filename

        doc = BufferedInputFile(file_resp.content, filename=filename)
        await bot.send_document(chat_id=int(chat_id), document=doc, caption=f"📄 {filename}")
        return True
    except Exception as e:
        logger.warning("Не удалось отправить файл %s в чат %s: %s", task_id, chat_id, e)
        return False


# ---------------------------------------------------------------------------
# Команды бота
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Приветствие и краткая инструкция."""
    await message.answer(
        "👋 <b>GrabVidZilla Bot</b>\n\n"
        "Отправьте мне ссылку на видео — я покажу доступные форматы "
        "и скачаю выбранный.\n\n"
        "Команды:\n"
        "/queue — активные задачи\n"
        "/history — последние 10 завершённых\n"
        "/cancel &lt;id&gt; — отменить задачу\n"
        "/help — помощь",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Список команд."""
    await message.answer(
        "📖 <b>Команды:</b>\n\n"
        "• Отправьте ссылку — анализ + выбор формата + скачивание\n"
        "• /queue — список активных задач (queued + downloading)\n"
        "• /history — последние 10 завершённых задач\n"
        "• /cancel &lt;id&gt; — отменить задачу (первые 8 символов ID)\n"
        "• /help — эта справка",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("queue"))
async def cmd_queue(message: Message) -> None:
    """Список активных задач."""
    try:
        resp = await _get_http().get("/downloads")
        resp.raise_for_status()
        tasks = resp.json()
    except Exception as e:
        await message.answer(f"❌ Ошибка получения списка задач: {e}")
        return

    active = [t for t in tasks if t["status"] in ("queued", "downloading")]
    if not active:
        await message.answer("📭 Нет активных задач.")
        return

    lines = []
    for t in active[:20]:
        status_icon = "⏳" if t["status"] == "queued" else "⏬"
        progress = t.get("progress", 0)
        speed = t.get("speed") or ""
        line = f"{status_icon} <code>{_short_id(t['id'])}</code> {progress:.0f}%"
        if speed:
            line += f" • {speed}"
        url_short = t["url"][:50] + ("…" if len(t["url"]) > 50 else "")
        line += f"\n   {url_short}"
        lines.append(line)

    await message.answer(
        f"📋 <b>Активные задачи ({len(active)}):</b>\n\n" + "\n\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    """Последние 10 завершённых задач."""
    try:
        resp = await _get_http().get("/downloads")
        resp.raise_for_status()
        tasks = resp.json()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        return

    finished = [
        t for t in tasks
        if t["status"] in ("completed", "error", "cancelled")
    ][:10]

    if not finished:
        await message.answer("📭 История пуста.")
        return

    lines = []
    buttons_rows = []
    for t in finished:
        if t["status"] == "completed":
            icon = "✅"
            file_size = t.get("file_size") or 0
            info = f"{html_escape(t.get('filename', '?'))} • {_format_size(file_size)}"
        elif t["status"] == "error":
            icon = "❌"
            file_size = 0
            info = html_escape(t.get("error_message", "Ошибка"))
        else:
            icon = "🚫"
            file_size = 0
            info = "Отменена"

        time_str = _time_ago(t.get("finished_at"))
        short = _short_id(t["id"])
        lines.append(f"{icon} <code>{short}</code> {info} • {time_str}")

        # Кнопки для completed задач
        if t["status"] == "completed":
            if file_size and file_size < TG_FILE_LIMIT:
                # ≤ 50 MB — кнопка «Скачать» (отправка файлом через callback)
                buttons_rows.append([InlineKeyboardButton(
                    text=f"📥 {short} — Скачать",
                    callback_data=f"file:{t['id']}",
                )])
            else:
                # > лимита — текстовая ссылка прямо в сообщении
                link = _download_link(t["id"])
                lines.append(f"   🔗 {link}")

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons_rows) if buttons_rows else None

    await message.answer(
        "📜 <b>Последние задачи:</b>\n\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message) -> None:
    """Отменить задачу по короткому ID (первые 8 символов UUID)."""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Использование: /cancel &lt;id&gt;", parse_mode=ParseMode.HTML)
        return

    short_id = parts[1].strip()

    # Ищем задачу по короткому ID
    try:
        resp = await _get_http().get("/downloads")
        resp.raise_for_status()
        tasks = resp.json()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        return

    match = None
    for t in tasks:
        if t["id"].startswith(short_id):
            match = t
            break

    if not match:
        await message.answer(f"❌ Задача с ID <code>{short_id}</code> не найдена.", parse_mode=ParseMode.HTML)
        return

    if match["status"] not in ("queued", "downloading"):
        await message.answer(f"⚠️ Задачу нельзя отменить (статус: {match['status']}).")
        return

    try:
        resp = await _get_http().delete(f"/downloads/{match['id']}")
        resp.raise_for_status()
        await message.answer(f"✅ Задача <code>{short_id}</code> отменена.", parse_mode=ParseMode.HTML)
    except Exception as e:
        await message.answer(f"❌ Ошибка отмены: {e}")


# ---------------------------------------------------------------------------
# Основной сценарий: пользователь отправляет ссылку
# ---------------------------------------------------------------------------

@router.message(F.text.regexp(r"https?://"))
async def handle_url(message: Message) -> None:
    """Обработка URL: анализ → выбор формата → скачивание."""
    url = (message.text or "").strip()

    status_msg = await message.answer("🔍 Анализирую ссылку...")

    # Шаг 1: анализ через API
    try:
        resp = await _get_http().get("/analyze", params={"url": url}, timeout=120)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.json().get("detail", "")
        except Exception:
            pass
        await status_msg.edit_text(f"❌ Ошибка анализа: {detail or e}")
        return
    except Exception as e:
        await status_msg.edit_text(f"❌ Не удалось проанализировать: {e}")
        return

    qualities = data.get("qualities", [])
    title = data.get("info", {}).get("title", "Видео")

    if not qualities:
        # Нет информации о форматах — скачиваем в лучшем качестве
        await status_msg.edit_text("⏳ Скачиваю в лучшем качестве...")
        await _start_and_track(message, status_msg, url, fmt=None, audio_only=False)
        return

    # Шаг 2: предлагаем выбор формата (callback_data ≤ 64 байт — используем кэш)
    buttons = []

    # Кнопка «лучшее качество» всегда первая
    best_key = _store_pending(url, fmt=None, audio_only=False)
    buttons.append([InlineKeyboardButton(
        text="🎬 Лучшее качество",
        callback_data=f"dl:{best_key}",  # "dl:a1b2c3d4" — 11 байт
    )])

    for q in qualities[:6]:  # максимум 6 кнопок
        if q == "audio only":
            key = _store_pending(url, fmt=None, audio_only=True)
            buttons.append([InlineKeyboardButton(
                text="🎵 Только аудио",
                callback_data=f"dl:{key}",
            )])
        else:
            height = q.replace("p", "")
            fmt_selector = f"bv*[height<={height}]+ba/best[height<={height}]"
            key = _store_pending(url, fmt=fmt_selector, audio_only=False)
            buttons.append([InlineKeyboardButton(
                text=f"🎬 {q}",
                callback_data=f"dl:{key}",
            )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await status_msg.edit_text(
        f"📹 <b>{title}</b>\n\nВыберите формат:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("dl:"))
async def cb_download(callback: CallbackQuery) -> None:
    """Обработка выбора формата — запуск скачивания."""
    await callback.answer()

    key = (callback.data or "").split(":", 1)[1]  # 8-символьный ключ из кэша
    params = _pop_pending(key)

    if params is None:
        # Кнопка устарела (бот был перезапущен)
        await callback.message.edit_text(
            "⚠️ Сессия выбора истекла. Отправьте ссылку снова."
        )
        return

    url = params["url"]
    fmt = params["fmt"]
    audio_only = params["audio_only"]

    status_msg = callback.message
    if status_msg:
        await status_msg.edit_text("⏳ Добавляю в очередь...")
        await _start_and_track(callback, status_msg, url, fmt=fmt, audio_only=audio_only)


@router.callback_query(F.data.startswith("file:"))
async def cb_send_file(callback: CallbackQuery) -> None:
    """Отправляет скачанный файл пользователю через _send_file_to_chat."""
    await callback.answer("📥 Загружаю файл...")

    task_id = (callback.data or "").split(":", 1)[1]
    chat_id = callback.message.chat.id
    bot = callback.message.bot

    ok = await _send_file_to_chat(bot, chat_id, task_id)
    if not ok:
        link = _download_link(task_id)
        await callback.message.answer(
            f"❌ Не удалось отправить файл.\n"
            f"🔗 {link}"
        )


@router.callback_query(F.data.startswith("convert:"))
async def cb_convert(callback: CallbackQuery) -> None:
    """Запускает конвертацию уже скачанного файла в MP4.

    Вызывает POST /downloads/{task_id}/convert через API,
    затем отслеживает прогресс новой задачи конвертации.
    """
    await callback.answer("🔄 Запускаю конвертацию...")

    task_id = (callback.data or "").split(":", 1)[1]
    chat_id = str(callback.message.chat.id)

    # Создаём задачу конвертации через API
    try:
        resp = await _get_http().post(
            f"/downloads/{task_id}/convert",
            params={"telegram_chat_id": chat_id},
        )
        resp.raise_for_status()
        new_task_id = resp.json()["id"]
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.json().get("detail", "")
        except Exception:
            pass
        await callback.message.answer(
            f"❌ Ошибка запуска конвертации: {detail or str(e)}",
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")
        return

    short = _short_id(new_task_id)
    # Отправляем отдельное сообщение для отображения прогресса конвертации
    status_msg = await callback.message.answer(
        f"🔄 Конвертация добавлена в очередь. ID: <code>{short}</code>",
        parse_mode=ParseMode.HTML,
    )

    # Отслеживаем прогресс — та же логика, что и при скачивании
    await _track_task(status_msg, new_task_id, chat_id, progress_label="Конвертация")


# ---------------------------------------------------------------------------
# Скачивание + отслеживание прогресса
# ---------------------------------------------------------------------------

async def _track_task(
    status_msg: Message,
    task_id: str,
    chat_id: str,
    progress_label: str = "Скачивание",
) -> None:
    """Отслеживает прогресс задачи, редактируя status_msg.

    Args:
        status_msg: сообщение для редактирования (индикатор прогресса).
        task_id: ID задачи в БД.
        chat_id: ID чата для отправки файла.
        progress_label: метка в строке прогресса («Скачивание» или «Конвертация»).
    """
    short = _short_id(task_id)
    last_text = ""

    while True:
        await asyncio.sleep(3)

        try:
            resp = await _get_http().get(f"/downloads/{task_id}")
            resp.raise_for_status()
            task = resp.json()
        except Exception:
            continue

        status = task["status"]

        if status == "downloading":
            progress = task.get("progress", 0)
            speed = task.get("speed") or ""
            eta = task.get("eta") or ""
            text = f"⏬ {progress_label}: {progress:.0f}%"
            if speed:
                text += f" • {speed}"
            if eta:
                text += f" • осталось {eta}"
            text += f"\nID: <code>{short}</code>"

            if text != last_text:
                try:
                    await status_msg.edit_text(text, parse_mode=ParseMode.HTML)
                    last_text = text
                except Exception:
                    pass  # message not modified

        elif status == "completed":
            filename = task.get("filename", "?")
            file_size = task.get("file_size") or 0

            text = (
                f"✅ Готово!\n"
                f"📄 {filename}\n"
                f"📦 {_format_size(file_size)}"
            )

            # Кнопка «Конвертировать в MP4» — только если файл не MP4
            ext = os.path.splitext(filename)[1].lower()
            convert_kb = None
            if ext and ext != ".mp4":
                convert_kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🔄 Конвертировать в MP4",
                        callback_data=f"convert:{task_id}",
                    )
                ]])

            bot = status_msg.bot
            if file_size and file_size < TG_FILE_LIMIT:
                # Файл ≤ лимита — отправляем как документ прямо в чат
                try:
                    await status_msg.edit_text(text + "\n\n📥 Отправляю файл...")
                except Exception:
                    pass

                ok = await _send_file_to_chat(bot, chat_id, task_id)
                if ok:
                    try:
                        await status_msg.edit_text(text, reply_markup=convert_kb)
                    except Exception:
                        pass
                else:
                    link = _download_link(task_id)
                    try:
                        await status_msg.edit_text(
                            text + f"\n\n🔗 {link}",
                            reply_markup=convert_kb,
                        )
                    except Exception:
                        pass
            else:
                # Файл > лимита — текстовая ссылка
                link = _download_link(task_id)
                try:
                    await status_msg.edit_text(
                        text + f"\n\n🔗 {link}\n(файл > 500 MB — скачайте по ссылке)",
                        reply_markup=convert_kb,
                    )
                except Exception:
                    pass
            break

        elif status == "error":
            err = task.get("error_message", "Неизвестная ошибка")
            try:
                await status_msg.edit_text(f"❌ Ошибка: {err}")
            except Exception:
                pass
            break

        elif status == "cancelled":
            try:
                await status_msg.edit_text("🚫 Задача отменена.")
            except Exception:
                pass
            break

        elif status == "queued":
            pass  # ждём


async def _start_and_track(
    event: Message | CallbackQuery,
    status_msg: Message,
    url: str,
    fmt: str | None,
    audio_only: bool,
) -> None:
    """Создаёт задачу через API и отслеживает прогресс, редактируя сообщение."""
    # Определяем chat_id
    if isinstance(event, CallbackQuery):
        chat_id = str(event.message.chat.id)
    else:
        chat_id = str(event.chat.id)

    # Создаём задачу
    payload = {
        "url": url,
        "audio_only": audio_only,
        "telegram_chat_id": chat_id,
    }
    if fmt:
        payload["format"] = fmt

    try:
        resp = await _get_http().post("/downloads", json=payload)
        resp.raise_for_status()
        task_id = resp.json()["id"]
    except Exception as e:
        await status_msg.edit_text(f"❌ Не удалось создать задачу: {e}")
        return

    short = _short_id(task_id)
    await status_msg.edit_text(f"⏳ Добавлено в очередь. ID: <code>{short}</code>", parse_mode=ParseMode.HTML)

    await _track_task(status_msg, task_id, chat_id, progress_label="Скачивание")


# ---------------------------------------------------------------------------
# Запуск бота
# ---------------------------------------------------------------------------

async def _run_bot() -> None:
    """Запускает бота с Long Polling."""
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не задан! Бот не может запуститься.")
        sys.exit(1)

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # Middleware для whitelist
    dp.message.middleware(WhitelistMiddleware())
    dp.callback_query.middleware(WhitelistMiddleware())

    dp.include_router(router)

    logger.info("Telegram-бот запущен (Long Polling).")
    try:
        await dp.start_polling(bot)
    finally:
        global _http
        if _http and not _http.is_closed:
            await _http.aclose()


def main() -> None:
    """Точка входа при запуске ``python -m bot.bot_main``.

    Оборачивает запуск в цикл с автоматическим перезапуском при потере
    соединения. Ctrl+C останавливает бота.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    while True:
        try:
            asyncio.run(_run_bot())
        except KeyboardInterrupt:
            logger.info("Бот остановлен вручную.")
            break
        except Exception as e:
            logger.error("Бот упал с ошибкой: %s. Перезапуск через 10 секунд...", e)
            time.sleep(10)


if __name__ == "__main__":
    main()
