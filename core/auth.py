"""User authentication and authorisation logic for GrabVidZilla.

Этот модуль содержит бизнес-логику, связанную с пользователями:
- ORM-модель ``User``;
- функции-репозитории для работы с пользователями;
- сервисные функции регистрации и аутентификации.

Важно: модуль не должен импортировать ``cli`` или ``ui`` и не выполнять
никакого вывода для пользователя. Все сообщения пользователю формируются
на уровне CLI/UI.

Пароли по требованию проекта хранятся в открытом виде в базе данных.
Это подходит только для локальной/учебной среды и не должно использоваться
в боевых системах.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.orm import Session

from core.db import Base


class User(Base):
    """ORM-модель пользователя.

    Поля:
        id: первичный ключ.
        email: уникальный e-mail пользователя.
        name: отображаемое имя.
        password: пароль в открытом виде (для учебных сценариев).
        phone: телефон (опционально).
        is_active: активен ли пользователь (False = заблокирован/отключён).
        is_admin: флаг администратора.
        role: строковая роль (например, 'admin' или 'user').
        created_at: время создания записи.
        updated_at: время последнего обновления записи.
    """

    __tablename__ = "users"

    id: int = Column(Integer, primary_key=True, index=True)
    email: str = Column(String(255), unique=True, index=True, nullable=False)
    name: str = Column(String(255), nullable=False)
    password: str = Column(String(255), nullable=False)
    phone: str | None = Column(String(64), nullable=True)
    is_active: bool = Column(Boolean, nullable=False, default=True)
    is_admin: bool = Column(Boolean, nullable=False, default=False)
    role: str = Column(String(32), nullable=False, default="user")
    created_at: datetime = Column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: datetime = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


@dataclass
class UserPublic:
    """Упрощённое представление пользователя для UI/API."""

    id: int
    email: str
    name: str
    role: str
    is_admin: bool
    is_active: bool


# ----------------------- Репозиторий пользователей ----------------------- #


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    """Возвращает пользователя по его идентификатору или None."""
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """Возвращает пользователя по e-mail или None."""
    return db.query(User).filter(User.email == email).first()


def get_user_by_name(db: Session, name: str) -> Optional[User]:
    """Возвращает пользователя по имени или None."""
    return db.query(User).filter(User.name == name).first()


def list_users(db: Session) -> list[User]:
    """Возвращает список всех пользователей, отсортированных по id."""
    return db.query(User).order_by(User.id.asc()).all()


def create_user(
    db: Session,
    email: str,
    name: str,
    password: str,
    phone: Optional[str] = None,
    role: str = "user",
    is_admin: bool = False,
    is_active: bool = True,
) -> User:
    """Создаёт и сохраняет нового пользователя в базе данных.

    Эта функция не выполняет сложной валидации и может быть использована
    для административных операций. Для пользовательской регистрации
    рекомендуется использовать ``register_user``.
    """
    if not email or "@" not in email:
        # Короткое и понятное сообщение об ошибке для UI/CLI
        raise ValueError("Некорректно написана почта.")
    if not name:
        raise ValueError("Имя пользователя не может быть пустым.")
    if not password:
        raise ValueError("Пароль не может быть пустым.")

    existing = get_user_by_email(db, email=email)
    if existing is not None:
        raise ValueError("Пользователь с таким email уже существует.")

    existing_name = get_user_by_name(db, name=name)
    if existing_name is not None:
        raise ValueError("Пользователь с таким именем уже существует.")

    if phone:
        existing_phone = db.query(User).filter(User.phone == phone).first()
        if existing_phone is not None:
            raise ValueError("Пользователь с таким телефоном уже существует.")

    now = datetime.utcnow()
    user = User(
        email=email.strip(),
        name=name.strip(),
        password=str(password),
        phone=phone.strip() if isinstance(phone, str) and phone.strip() else None,
        role=(role or "user").strip(),
        is_admin=bool(is_admin),
        is_active=bool(is_active),
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_user(
    db: Session,
    user_id: int,
    *,
    email: Optional[str] = None,
    name: Optional[str] = None,
    password: Optional[str] = None,
    phone: Optional[str] = None,
    role: Optional[str] = None,
    is_admin: Optional[bool] = None,
    is_active: Optional[bool] = None,
) -> User:
    """Обновляет данные пользователя и возвращает обновлённый объект.

    Только явно переданные аргументы изменяются.
    """
    user = get_user_by_id(db, user_id)
    if user is None:
        raise ValueError("Пользователь не найден.")

    # Обновление email с проверкой уникальности
    if email is not None and email != user.email:
        if not email or "@" not in email:
            raise ValueError("Некорректно написана почта.")
        existing = get_user_by_email(db, email=email)
        if existing is not None and existing.id != user.id:
            raise ValueError("Пользователь с таким email уже существует.")
        user.email = email

    if name is not None:
        if not name:
            raise ValueError("Имя пользователя не может быть пустым.")
        existing_name = get_user_by_name(db, name=name)
        if existing_name is not None and existing_name.id != user.id:
            raise ValueError("Пользователь с таким именем уже существует.")
        user.name = name
    if password is not None:
        if not password:
            raise ValueError("Пароль не может быть пустым.")
        user.password = str(password)
    if phone is not None:
        if phone.strip():
            existing_phone = (
                db.query(User)
                .filter(User.phone == phone.strip(), User.id != user.id)
                .first()
            )
            if existing_phone is not None:
                raise ValueError("Пользователь с таким телефоном уже существует.")
        user.phone = phone.strip() if phone.strip() else None
    if role is not None:
        user.role = role.strip() or user.role
    if is_admin is not None:
        user.is_admin = bool(is_admin)
    if is_active is not None:
        user.is_active = bool(is_active)

    user.updated_at = datetime.utcnow()
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def deactivate_user(db: Session, user_id: int) -> User:
    """Отключает пользователя (is_active=False) и возвращает обновлённый объект."""
    return update_user(db, user_id, is_active=False)


def delete_user(db: Session, user_id: int) -> None:
    """Удаляет пользователя из базы данных.

    Если пользователь не найден, возбуждает ValueError.
    Логику проверки прав (кто может кого удалять) следует реализовывать
    на уровне UI/CLI; здесь только низкоуровневое удаление.
    """
    user = get_user_by_id(db, user_id)
    if user is None:
        raise ValueError("Пользователь не найден.")
    db.delete(user)
    db.commit()


# --------------------- Сервисные функции аутентификации ------------------ #


def register_user(
    db: Session,
    email: str,
    name: str,
    password: str,
    phone: Optional[str] = None,
    role: str = "user",
    is_admin: bool = False,
    is_active: bool = True,
) -> User:
    """Регистрирует нового пользователя.

    Обёртка над :func:`create_user` с понятными сообщениями об ошибках.
    """
    try:
        return create_user(
            db=db,
            email=email,
            name=name,
            password=password,
            phone=phone,
            role=role,
            is_admin=is_admin,
            is_active=is_active,
        )
    except ValueError as exc:
        # Пробрасываем как есть, чтобы UI/CLI могли красиво отобразить.
        raise
    except Exception as exc:  # pragma: no cover - неожиданные ошибки
        raise RuntimeError(f"Не удалось зарегистрировать пользователя: {exc}") from exc


def authenticate_user(db: Session, email: str, password: str) -> User:
    """Проверяет логин (email или имя) и пароль и возвращает пользователя при успехе.

    Raises:
        ValueError: при неправильном email/пароле или неактивном пользователе.
    """
    if not email or not password:
        raise ValueError("Email/имя и пароль обязательны.")

    login_value = email.strip()

    # Сначала пробуем как email, затем как имя пользователя.
    user = get_user_by_email(db, email=login_value)
    if user is None:
        user = get_user_by_name(db, name=login_value)
    # Чтобы не раскрывать, существовал ли email, используем общее сообщение.
    if user is None or str(user.password) != str(password):
        raise ValueError("Неверный логин или пароль.")

    if not user.is_active:
        raise ValueError("Учётная запись отключена. Обратитесь к администратору.")

    return user


def user_is_admin(user: User) -> bool:
    """Возвращает True, если пользователь является администратором (admin/root)."""
    if not isinstance(user, User):
        return False
    if user.is_admin:
        return True
    role_lower = (user.role or "").lower()
    return role_lower in ("admin", "root")


def user_is_root(user: User) -> bool:
    """Возвращает True, если пользователь является root-пользователем."""
    if not isinstance(user, User):
        return False
    return (user.role or "").lower() == "root"


def to_public(user: User) -> UserPublic:
    """Преобразует ORM-модель в упрощённое представление для UI/API."""
    return UserPublic(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        is_admin=user.is_admin,
        is_active=user.is_active,
    )


def user_to_dict(user: User) -> dict:
    """Удобный помощник для получения словаря с публичными полями пользователя."""
    return asdict(to_public(user))


