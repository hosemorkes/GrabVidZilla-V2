"""Модуль системного логирования событий GrabVidZilla.

Предоставляет единственную публичную функцию ``log_event``, которая
сохраняет структурированную запись о событии в таблицу ``system_logs``.

Принцип «лог никогда не роняет приложение»: все исключения внутри
``log_event`` перехватываются; при ошибке пишем в стандартный Python
``logging`` и возвращаем ``None``.

Пример использования::

    from sqlalchemy.orm import Session
    from core.logger import log_event

    log_event(
        session=db,
        level="INFO",
        source="api",
        event_type="download_started",
        message="Задача скачивания создана",
        user_id=42,
        task_id=7,
        details={"url": "https://example.com/video"},
        ip_address="192.168.1.1",
    )
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy.orm import Session

from core.db import SystemLog

# Стандартный Python-логгер для самого модуля logger.py.
# Используется ТОЛЬКО для вывода ошибок при сбое записи в БД,
# чтобы не создавать рекурсию через log_event.
_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Константы допустимых значений
# ---------------------------------------------------------------------------

LEVELS: frozenset[str] = frozenset({"INFO", "WARNING", "ERROR", "CRITICAL"})
"""Допустимые уровни логирования."""

SOURCES: frozenset[str] = frozenset({"api", "worker", "bot", "cli"})
"""Допустимые источники — слои приложения."""

EVENT_TYPES: frozenset[str] = frozenset({
    # Аутентификация
    "login",
    "login_failed",
    "logout",
    # Управление пользователями
    "user_created",
    "user_deactivated",
    # Скачивание
    "download_started",
    "download_completed",
    "download_failed",
    "download_cancelled",
    # Конвертация
    "convert_started",
    "convert_completed",
    "convert_failed",
    # Системные события
    "system_startup",
    "system_shutdown",
    "cleanup_ran",
})
"""Допустимые типы событий. Используйте одно из этих значений в ``event_type``."""


# ---------------------------------------------------------------------------
# Публичная функция
# ---------------------------------------------------------------------------

def log_event(
    session: Session,
    level: str,
    source: str,
    event_type: str,
    message: str,
    user_id: Optional[int] = None,
    task_id: Optional[int] = None,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> Optional[SystemLog]:
    """Записывает событие в таблицу system_logs и возвращает созданный объект.

    Функция безопасна для вызова в любом месте приложения: исключения при
    записи перехватываются, приложение продолжает работу.

    Параметры:
        session     — активная SQLAlchemy-сессия (обычно из ``get_db()``).
        level       — уровень важности: ``"INFO"``, ``"WARNING"``,
                      ``"ERROR"`` или ``"CRITICAL"``. Нечувствителен к
                      регистру. Неизвестное значение заменяется на ``"INFO"``.
        source      — слой-источник: ``"api"``, ``"worker"``, ``"bot"``
                      или ``"cli"``. Неизвестное значение сохраняется как есть
                      (для расширяемости), но пишется предупреждение.
        event_type  — тип события из ``EVENT_TYPES``. Неизвестное значение
                      сохраняется как есть; пишется предупреждение.
        message     — краткое человекочитаемое описание события.
        user_id     — опциональный ID пользователя (FK → users.id).
        task_id     — опциональный ID задачи скачивания (FK → downloads.id).
        details     — опциональный словарь с дополнительными данными;
                      сериализуется в JSON-строку перед сохранением.
        ip_address  — опциональный IP-адрес инициатора запроса.

    Возвращает:
        Сохранённый объект ``SystemLog`` или ``None`` при ошибке.
    """
    try:
        # --- Нормализация level -------------------------------------------
        normalized_level = level.upper() if isinstance(level, str) else "INFO"
        if normalized_level not in LEVELS:
            normalized_level = "INFO"

        # --- Проверка source (мягкая, не блокирующая) ---------------------
        if source not in SOURCES:
            _log.warning(
                "log_event: неизвестный source=%r, ожидается один из %s",
                source, sorted(SOURCES),
            )

        # --- Проверка event_type (мягкая, не блокирующая) -----------------
        if event_type not in EVENT_TYPES:
            _log.warning(
                "log_event: неизвестный event_type=%r, ожидается один из %s",
                event_type, sorted(EVENT_TYPES),
            )

        # --- Сериализация details в JSON-строку ---------------------------
        details_str: Optional[str] = None
        if details is not None:
            details_str = json.dumps(details, ensure_ascii=False)

        # --- Создание и сохранение записи ---------------------------------
        entry = SystemLog(
            level=normalized_level,
            source=source,
            event_type=event_type,
            message=message,
            user_id=user_id,
            task_id=task_id,
            details=details_str,
            ip_address=ip_address,
        )
        session.add(entry)
        session.commit()
        return entry

    except Exception as exc:
        # Ошибка лога НЕ должна ронять основное приложение.
        # Записываем в стандартный Python-логгер и возвращаем None.
        _log.error(
            "log_event: не удалось записать событие %r/%r: %s",
            source, event_type, exc, exc_info=True,
        )
        return None
