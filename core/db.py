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
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine
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

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


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


