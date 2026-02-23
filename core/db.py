"""Database setup module for GrabVidZilla.

This module provides a simple wrapper around SQLAlchemy for working with a
SQLite database stored in ``data/app.db``. It exposes the SQLAlchemy engine,
session factory and declarative base, as well as an ``init_db`` helper that
creates all tables.

Модуль не зависит от CLI или UI и не выполняет никаких операций ввода/вывода
для пользователя. Вся логика сообщений и форматирования должна находиться
в верхних слоях приложения.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Generator

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer, String, Text,
    create_engine, event, text as sa_text,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# Путь к файлу SQLite по умолчанию: ./data/app.db
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "app.db"

# Строка подключения к SQLite. ``check_same_thread=False`` позволяет
# переиспользовать соединение между потоками (актуально для Streamlit/FastAPI).
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """Настраиваем SQLite для безопасной работы из нескольких процессов.

    WAL-режим позволяет одновременное чтение и запись из разных процессов
    (API + Worker). ``busy_timeout`` — время ожидания при блокировке.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class Download(Base):
    """Модель задачи скачивания, хранится в SQLite для персистентности.

    Поля повторяют жизненный цикл задачи: от создания (queued) до завершения
    (completed / error / cancelled). TTL-поле позволяет автоматически удалять
    устаревшие записи и файлы.
    """

    __tablename__ = "downloads"

    id: str = Column(String, primary_key=True)                # UUID задачи
    url: str = Column(String, nullable=False)                  # исходная ссылка
    status: str = Column(String, default="queued")             # queued / downloading / completed / error / cancelled
    progress: float = Column(Float, default=0.0)               # 0.0 – 100.0
    speed: str | None = Column(String, nullable=True)          # "1.2 MiB/s" или None
    eta: str | None = Column(String, nullable=True)            # "00:01:23" или None
    output_path: str | None = Column(String, nullable=True)    # абсолютный путь к файлу
    filename: str | None = Column(String, nullable=True)       # только имя файла
    file_size: int | None = Column(Integer, nullable=True)     # байты
    sha256: str | None = Column(String, nullable=True)         # контрольная сумма
    format_id: str | None = Column(String, nullable=True)      # запрошенный format
    audio_only: bool = Column(Boolean, default=False)
    subtitle_lang: str | None = Column(String, nullable=True)
    cookies_path: str | None = Column(String, nullable=True)
    error_message: str | None = Column(String, nullable=True)  # текст ошибки для пользователя
    error_type: str | None = Column(String, nullable=True)     # категория ошибки
    error_details: str | None = Column(Text, nullable=True)    # полный traceback
    created_at: datetime = Column(DateTime, default=datetime.utcnow)
    started_at: datetime | None = Column(DateTime, nullable=True)
    finished_at: datetime | None = Column(DateTime, nullable=True)
    ttl_expires_at: datetime | None = Column(DateTime, nullable=True)
    cancellation_requested: bool = Column(Boolean, default=False)  # Worker проверяет в progress_callback
    webhook_url: str | None = Column(String, nullable=True)        # URL для POST-уведомления при завершении
    webhook_sent: bool = Column(Boolean, default=False)            # флаг: webhook уже отправлен
    telegram_chat_id: str | None = Column(String, nullable=True)   # chat_id инициатора в Telegram

    def to_dict(self) -> dict:
        """Сериализация записи в словарь для API-ответов."""
        return {
            "id": self.id,
            "url": self.url,
            "status": self.status,
            "progress": self.progress,
            "speed": self.speed,
            "eta": self.eta,
            "output_path": self.output_path,
            "filename": self.filename,
            "file_size": self.file_size,
            "sha256": self.sha256,
            "format_id": self.format_id,
            "audio_only": self.audio_only,
            "subtitle_lang": self.subtitle_lang,
            "cookies_path": self.cookies_path,
            "error_message": self.error_message,
            "error_type": self.error_type,
            "error_details": self.error_details,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "ttl_expires_at": self.ttl_expires_at.isoformat() if self.ttl_expires_at else None,
            "cancellation_requested": self.cancellation_requested,
            "webhook_url": self.webhook_url,
            "webhook_sent": self.webhook_sent,
            "telegram_chat_id": self.telegram_chat_id,
        }


def get_db() -> Generator[Session, None, None]:
    """Yield a database session and ensure it is closed afterwards.

    This helper is primarily useful for FastAPI dependency injection and for
    contexts where explicit session lifetime management is desired.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Initialise the database and create all tables if they do not exist.

    This function:
    - гарантирует существование папки ``data``;
    - импортирует модель ``User`` из ``core.auth`` (чтобы она была
      зарегистрирована в SQLAlchemy);
    - вызывает ``Base.metadata.create_all`` для создания таблиц.

    Важно: импорт ``core.auth`` размещён внутри функции, чтобы избежать
    циклических импортов на уровне модулей.
    """
    # Создаём папку для файла БД, если её нет.
    os.makedirs(DATA_DIR, exist_ok=True)

    # Локальный импорт, чтобы не создавать циклическую зависимость при импорте
    # модулей. Модуль ``core.auth`` определяет модель User, наследующую Base.
    from core import auth as auth_module  # noqa: F401  # pylint: disable=unused-import

    # Создаём таблицы для всех моделей, зарегистрированных в Base.metadata.
    Base.metadata.create_all(bind=engine)

    # Миграция: добавляем новые колонки, если их ещё нет в существующей БД.
    # SQLAlchemy create_all не добавляет колонки в уже существующие таблицы.
    _migrate_add_missing_columns()


def _migrate_add_missing_columns() -> None:
    """Добавляет новые колонки в существующие таблицы (простая миграция).

    Проверяет наличие колонки через ``PRAGMA table_info`` и добавляет
    через ``ALTER TABLE``, если её нет.
    """
    with engine.connect() as conn:
        # Получаем список существующих колонок таблицы downloads
        result = conn.execute(sa_text("PRAGMA table_info(downloads)"))
        existing_columns = {row[1] for row in result}

        if "cancellation_requested" not in existing_columns:
            conn.execute(
                sa_text(
                    "ALTER TABLE downloads "
                    "ADD COLUMN cancellation_requested BOOLEAN DEFAULT 0"
                )
            )
            conn.commit()

        # Фаза 4: webhook + Telegram
        for col_name, col_def in [
            ("webhook_url", "VARCHAR"),
            ("webhook_sent", "BOOLEAN DEFAULT 0"),
            ("telegram_chat_id", "VARCHAR"),
        ]:
            if col_name not in existing_columns:
                conn.execute(
                    sa_text(f"ALTER TABLE downloads ADD COLUMN {col_name} {col_def}")
                )
                conn.commit()
