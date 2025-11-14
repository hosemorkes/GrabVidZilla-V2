"""Утилита для создания первого пользователя в GrabVidZilla.

Запускать из корня проекта:

    python scripts/create_first_user.py

Скрипт:
    - инициализирует базу данных (data/app.db);
    - проверяет, есть ли уже пользователи;
    - если есть — выводит сообщение и ничего не делает;
    - если нет — интерактивно спрашивает данные и создаёт первого пользователя (admin).
"""

from __future__ import annotations

from pathlib import Path

import sys

from rich.console import Console

# Добавляем корень проекта в sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.db import SessionLocal, init_db  # type: ignore  # noqa: E402
from core import auth as auth_core  # type: ignore  # noqa: E402


console = Console()


def main() -> None:
    """Точка входа скрипта создания первого пользователя."""
    console.print("\n[bold cyan]=== GrabVidZilla: создание первого пользователя ===[/bold cyan]\n")

    # Инициализируем базу и таблицы
    init_db()

    db = SessionLocal()
    try:
        # Проверим, есть ли уже хотя бы один пользователь
        existing = db.query(auth_core.User.id).first()
        if existing is not None:
            console.print(
                "\n[bold yellow]⚠ Пользователь уже существует. "
                "Скрипт ничего не сделал.[/bold yellow]\n"
            )
            return

        console.print(
            "[green]В базе ещё нет пользователей. "
            "Создадим первого пользователя с правами ROOT.[/green]\n"
        )
        email = input("Email: ").strip()
        name = input("Имя: ").strip()
        password = input("Пароль: ").strip()
        phone = input("Телефон (опционально, можно оставить пустым): ").strip()

        if not email or not name or not password:
            console.print(
                "\n[bold red]❌ Ошибка[/bold red]\n"
                "[red]Email, имя и пароль обязательны. Пользователь не создан.[/red]\n"
            )
            return

        try:
            user = auth_core.register_user(
                db=db,
                email=email,
                name=name,
                password=password,
                phone=phone or None,
                role="root",
                is_admin=True,
                is_active=True,
            )
        except ValueError as exc:
            # Показываем только текст ошибки без трейсбека, красиво оформленный
            console.print(
                "\n[bold red]❌ Ошибка[/bold red]\n"
                f"[red]{str(exc)}[/red]\n"
            )
            return
        except Exception as exc:
            # Непредвиденные ошибки тоже выводим без трейсбека
            console.print(
                "\n[bold red]❌ Неизвестная ошибка[/bold red]\n"
                f"[red]{exc}[/red]\n"
            )
            return
        console.print(
            "\n[bold green]✅ Пользователь создан[/bold green]\n"
            f"[green]id={user.id}, email={user.email}, "
            f"роль={user.role}, is_admin={user.is_admin}[/green]\n"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()


