"""Тесты для модуля core.auth (регистрация, логин, роли).

Для изоляции от основной БД используем отдельный SQLite in-memory
и собственный Base/engine в рамках тестового модуля.
"""

from __future__ import annotations

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from core import auth as auth_core


def _setup_test_db() -> Session:
    """Создаёт in-memory SQLite и возвращает сессию."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    # Используем Base из core.auth, чтобы модель User была общей
    auth_core.Base.metadata.create_all(bind=engine)
    return TestingSessionLocal()


def _get_session() -> Generator[Session, None, None]:
    db = _setup_test_db()
    try:
        yield db
    finally:
        db.close()


def test_register_user_success() -> None:
    """Успешная регистрация нового пользователя."""
    for db in _get_session():
        user = auth_core.register_user(
            db=db,
            email="user@example.com",
            name="User",
            password="secret",
            phone="123",
            role="user",
            is_admin=False,
        )
        assert user.id is not None
        assert user.email == "user@example.com"
        assert user.role == "user"
        assert user.is_admin is False
        assert user.is_active is True


def test_register_user_duplicate_email() -> None:
    """Ошибка при попытке зарегистрировать пользователя с уже существующим email."""
    for db in _get_session():
        auth_core.register_user(
            db=db,
            email="dupe@example.com",
            name="User1",
            password="pass1",
            phone=None,
            role="user",
            is_admin=False,
        )
        try:
            auth_core.register_user(
                db=db,
                email="dupe@example.com",
                name="User2",
                password="pass2",
                phone=None,
                role="user",
                is_admin=False,
            )
            assert False, "Ожидалась ошибка при регистрации с дублирующим email"
        except ValueError as exc:
            assert "уже существует" in str(exc)


def test_register_user_duplicate_name() -> None:
    """Ошибка при попытке зарегистрировать пользователя с уже существующим именем."""
    for db in _get_session():
        auth_core.register_user(
            db=db,
            email="user1@example.com",
            name="SameName",
            password="pass1",
            phone=None,
            role="user",
            is_admin=False,
        )
        try:
            auth_core.register_user(
                db=db,
                email="user2@example.com",
                name="SameName",
                password="pass2",
                phone=None,
                role="user",
                is_admin=False,
            )
            assert False, "Ожидалась ошибка при регистрации с дублирующим именем"
        except ValueError as exc:
            assert "именем" in str(exc)


def test_register_user_duplicate_phone() -> None:
    """Ошибка при попытке зарегистрировать пользователя с уже существующим телефоном."""
    for db in _get_session():
        auth_core.register_user(
            db=db,
            email="user3@example.com",
            name="User3",
            password="pass3",
            phone="12345",
            role="user",
            is_admin=False,
        )
        try:
            auth_core.register_user(
                db=db,
                email="user4@example.com",
                name="User4",
                password="pass4",
                phone="12345",
                role="user",
                is_admin=False,
            )
            assert False, "Ожидалась ошибка при регистрации с дублирующим телефоном"
        except ValueError as exc:
            assert "телефоном" in str(exc)


def test_authenticate_user_success() -> None:
    """Успешный логин с корректными email и паролем."""
    for db in _get_session():
        auth_core.register_user(
            db=db,
            email="login@example.com",
            name="LoginUser",
            password="mypassword",
            phone=None,
            role="user",
            is_admin=False,
        )
        user = auth_core.authenticate_user(db, email="login@example.com", password="mypassword")
        assert user.email == "login@example.com"


def test_authenticate_user_by_name_success() -> None:
    """Успешный логин по имени пользователя."""
    for db in _get_session():
        auth_core.register_user(
            db=db,
            email="login2@example.com",
            name="LoginUser2",
            password="mypassword2",
            phone=None,
            role="user",
            is_admin=False,
        )
        user = auth_core.authenticate_user(db, email="LoginUser2", password="mypassword2")
        assert user.name == "LoginUser2"


def test_authenticate_user_wrong_password() -> None:
    """Неуспешный логин при неверном пароле."""
    for db in _get_session():
        auth_core.register_user(
            db=db,
            email="wrongpass@example.com",
            name="User",
            password="correct",
            phone=None,
            role="user",
            is_admin=False,
        )
        try:
            auth_core.authenticate_user(db, email="wrongpass@example.com", password="incorrect")
            assert False, "Ожидалась ошибка при неверном пароле"
        except ValueError as exc:
            assert "Неверный логин или пароль" in str(exc)


def test_authenticate_inactive_user() -> None:
    """Неуспешный логин для неактивного пользователя."""
    for db in _get_session():
        user = auth_core.register_user(
            db=db,
            email="inactive@example.com",
            name="User",
            password="pass",
            phone=None,
            role="user",
            is_admin=False,
        )
        auth_core.update_user(db, user_id=user.id, is_active=False)
        try:
            auth_core.authenticate_user(db, email="inactive@example.com", password="pass")
            assert False, "Ожидалась ошибка при логине неактивного пользователя"
        except ValueError as exc:
            assert "отключена" in str(exc)


def test_user_roles_and_admin_flag() -> None:
    """Проверка различий прав для ролей root, admin и user."""
    for db in _get_session():
        root = auth_core.register_user(
            db=db,
            email="root@example.com",
            name="Root",
            password="rootpass",
            phone=None,
            role="root",
            is_admin=True,
        )
        admin = auth_core.register_user(
            db=db,
            email="admin@example.com",
            name="Admin",
            password="adminpass",
            phone=None,
            role="admin",
            is_admin=True,
        )
        user = auth_core.register_user(
            db=db,
            email="user2@example.com",
            name="User2",
            password="userpass",
            phone=None,
            role="user",
            is_admin=False,
        )

        assert auth_core.user_is_admin(root) is True
        assert auth_core.user_is_root(root) is True
        assert auth_core.user_is_admin(admin) is True
        assert auth_core.user_is_admin(user) is False

        # Удаление пользователя как root
        auth_core.delete_user(db, user_id=user.id)
        assert auth_core.get_user_by_id(db, user.id) is None


