"""Классификация ошибок скачивания.

Единый модуль для определения категории ошибки по тексту исключения.
Используется в Worker и (при необходимости) в API.
"""

from __future__ import annotations


def classify_download_error(e: Exception) -> tuple[str, str]:
    """Определяет категорию и читаемое сообщение по тексту исключения.

    Возвращает ``(error_type, error_message)``. Порядок проверок — от
    специфичных к общим.

    Args:
        e: Исключение, возникшее при скачивании.

    Returns:
        Кортеж (error_type, error_message).
    """
    msg = str(e).lower()

    if "sign in" in msg or "age" in msg or "login" in msg:
        return "auth_required", "Требуется авторизация или подтверждение возраста"
    if "private video" in msg:
        return "private_video", "Видео является приватным"
    if "has been removed" in msg or "account has been terminated" in msg:
        return "removed_video", "Видео было удалено"
    if "not available in your country" in msg or "geo" in msg:
        return "geo_blocked", "Видео недоступно в вашем регионе"
    if "video unavailable" in msg or "does not exist" in msg or "404" in msg:
        return "not_found", "Видео не найдено"
    if "is currently live" in msg or "live stream" in msg:
        return "live_stream", "Нельзя скачать активный прямой эфир"
    if "requested format" in msg or ("format" in msg and "not available" in msg):
        return "format_unavailable", "Запрошенный формат недоступен"
    if "429" in msg or "too many requests" in msg or "rate limit" in msg:
        return "rate_limited", "Платформа ограничила запросы, попробуйте позже"
    if "timed out" in msg or "timeout" in msg:
        return "download_timeout", "Превышено время ожидания"
    if isinstance(e, OSError) or "no space left" in msg or "disk" in msg:
        return "disk_full", "Недостаточно места на диске"
    if "ffmpeg" in msg:
        return "ffmpeg_error", "Ошибка обработки через ffmpeg"
    if "unsupported url" in msg or "unsupported site" in msg:
        return "unsupported_site", "Сайт не поддерживается"
    if isinstance(e, (ConnectionError, OSError)) or "network" in msg or "dns" in msg:
        return "network_error", "Ошибка сети"

    return "unknown", f"Неизвестная ошибка: {str(e)[:200]}"
