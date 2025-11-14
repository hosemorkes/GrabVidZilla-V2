"""Streamlit UI helpers for user authentication and authorisation.

Этот модуль отвечает только за визуальные формы (логин/регистрация) и работу
с ``st.session_state``. Вся бизнес-логика пользователей находится в ``core.auth``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import sys
import streamlit as st

# Гарантируем, что корень проекта есть в sys.path для импорта core.*
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.db import SessionLocal, init_db  # type: ignore  # noqa: E402
from core import auth as auth_core  # type: ignore  # noqa: E402


def _get_db_session():
    """Возвращает новый экземпляр сессии БД.

    В UI мы открываем короткоживущие сессии на время обработки формы.
    """
    return SessionLocal()


def _ensure_auth_state() -> None:
    """Гарантирует наличие ключа current_user в session_state."""
    if "current_user" not in st.session_state:
        st.session_state["current_user"] = None


def _has_any_users() -> bool:
    """Проверяет, есть ли в базе данных хотя бы один пользователь."""
    db = _get_db_session()
    try:
        return db.query(auth_core.User.id).first() is not None
    finally:
        db.close()


def _render_login_form() -> None:
    """Отрисовывает форму входа пользователя."""
    st.subheader("Вход")
    st.caption("Для входа можно использовать почту или имя.")
    email = st.text_input("Email (почта или имя)", key="auth_login_email")
    password = st.text_input("Пароль", type="password", key="auth_login_password")
    if st.button("Войти", key="auth_login_submit"):
        if not email or not password:
            st.error("Введите email и пароль.")
            return
        db = _get_db_session()
        try:
            user = auth_core.authenticate_user(db, email=email, password=password)
            st.session_state["current_user"] = auth_core.user_to_dict(user)
            # Мгновенно перерисовываем приложение, чтобы убрать блок логина/регистрации
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
        except Exception as exc:  # pragma: no cover - неожиданные ошибки
            st.error(f"Ошибка входа: {exc}")
        finally:
            db.close()


def _render_register_form() -> None:
    """Отрисовывает форму регистрации нового пользователя."""
    st.subheader("Регистрация")
    email = st.text_input("Email", key="auth_reg_email")
    name = st.text_input("Имя", key="auth_reg_name")
    password = st.text_input("Пароль", type="password", key="auth_reg_password")
    password_confirm = st.text_input(
        "Повторите пароль", type="password", key="auth_reg_password_confirm"
    )
    phone = st.text_input("Телефон (опционально)", key="auth_reg_phone")

    if st.button("Создать аккаунт", key="auth_reg_submit"):
        if not email or not name or not password or not password_confirm:
            st.error("Email, имя и оба поля пароля обязательны.")
            return
        if password != password_confirm:
            st.error("Пароли в обоих полях должны совпадать.")
            return
        db = _get_db_session()
        try:
            user = auth_core.register_user(
                db=db,
                email=email,
                name=name,
                password=password,
                phone=phone or None,
                is_active=True,
            )
            st.success("Учётная запись успешно создана. Теперь вы можете войти.")
        except ValueError as exc:
            st.error(str(exc))
        except Exception as exc:  # pragma: no cover - неожиданные ошибки
            st.error(f"Ошибка регистрации: {exc}")
        finally:
            db.close()


def render_auth_block() -> None:
    """Рендерит блок аутентификации в верхней части UI.

    Если пользователь не залогинен, отображает вкладки Вход/Регистрация.
    Если залогинен — показывает информацию о пользователе и кнопку выхода.
    """
    _ensure_auth_state()
    init_db()

    # Если в базе ещё нет ни одного пользователя, сначала нужно создать
    # первого пользователя через CLI-скрипт.
    if not _has_any_users():
        st.warning(
            "В базе ещё нет ни одного пользователя.\n\n"
            "Пожалуйста, сначала создайте первого пользователя через CLI:\n\n"
            "```bash\n"
            "python scripts/create_first_user.py\n"
            "```"
        )
        st.stop()

    user = st.session_state.get("current_user")

    # Если пользователь уже авторизован, не показываем блок авторизации вовсе
    # (на странице остаётся только основное меню).
    if user:
        return

    with st.container():
        tab_login, tab_register = st.tabs(["Вход", "Регистрация"])
        with tab_login:
            _render_login_form()
        with tab_register:
            _render_register_form()


def require_login() -> None:
    """Гарантирует, что пользователь авторизован.

    Если пользователь не авторизован, отображает блок логина/регистрации
    и останавливает дальнейшее выполнение страницы.
    """
    _ensure_auth_state()
    if not st.session_state.get("current_user"):
        st.info("Для доступа к этому разделу требуется вход в систему.")
        st.stop()


def require_admin() -> None:
    """Гарантирует, что текущий пользователь является администратором."""
    _ensure_auth_state()
    require_login()
    user_dict = st.session_state.get("current_user") or {}
    db = _get_db_session()
    try:
        user = auth_core.get_user_by_id(db, user_id=int(user_dict.get("id")))
    finally:
        db.close()

    if user is None or not auth_core.user_is_admin(user):
        st.error("У вас нет прав администратора для доступа к этому разделу.")
        st.stop()


def logout() -> None:
    """Выходит из текущей учётной записи."""
    _ensure_auth_state()
    st.session_state["current_user"] = None


def render_admin_panel() -> None:
    """Рисует панель администратора для управления пользователями.

    Доступна только для пользователей с правами admin.
    """
    require_admin()
    st.subheader("Управление пользователями")

    current_user_dict = st.session_state.get("current_user") or {}
    current_user_id = current_user_dict.get("id")
    current_is_root = False
    if current_user_id is not None:
        db_current = _get_db_session()
        try:
            cu = auth_core.get_user_by_id(db_current, user_id=int(current_user_id))
            current_is_root = bool(cu and auth_core.user_is_root(cu))
        finally:
            db_current.close()

    # Загружаем список пользователей для отображения и редактирования
    db = _get_db_session()
    try:
        users = auth_core.list_users(db)
    finally:
        db.close()

    if not users:
        st.info("В базе пока нет пользователей.")
        return

    # Подготовим данные для таблицы
    table_rows: list[dict] = []
    for u in users:
        pub = auth_core.user_to_dict(u)
        table_rows.append(
            {
                "id": pub["id"],
                "email": pub["email"],
                "name": pub["name"],
                "role": pub["role"],
                "is_admin": pub["is_admin"],
                "is_active": pub["is_active"],
                "phone": u.phone or "",
            }
        )

    st.dataframe(table_rows, use_container_width=True, hide_index=True)

    col_left, col_right = st.columns(2)

    # Редактирование существующего пользователя
    with col_left:
        st.markdown("**Редактировать пользователя**")
        user_ids = [row["id"] for row in table_rows]
        id_to_label = {
            row["id"]: f'{row["id"]} — {row["email"]} ({row["name"]})'
            for row in table_rows
        }
        selected_id = st.selectbox(
            "Пользователь",
            options=user_ids,
            format_func=lambda uid: id_to_label.get(uid, str(uid)),
        )
        selected_user = next(u for u in users if u.id == selected_id)
        is_root_user = auth_core.user_is_root(selected_user)

        # Если выбран root-пользователь и текущий не root — запрет на редактирование
        if is_root_user and not current_is_root:
            st.info(
                "Пользователь с ролью ROOT не может быть изменён. "
                "Изменять его данные может только он сам."
            )
        else:
            with st.form(f"edit_user_form_{selected_id}"):
                new_email = st.text_input("Email", value=selected_user.email)
                new_name = st.text_input("Имя", value=selected_user.name)
                new_phone = st.text_input("Телефон", value=selected_user.phone or "")
                new_password = st.text_input(
                    "Новый пароль (опционально)", type="password"
                )

                # Для root-пользователя не даём менять роль/флаги,
                # только контактные данные и пароль.
                if is_root_user:
                    st.caption("Роль ROOT и права администратора изменить нельзя.")
                    new_role = None
                    new_is_admin = None
                    new_is_active = None
                    can_delete = False
                else:
                    new_role = st.selectbox(
                        "Роль",
                        options=["user", "admin"],
                        index=0 if (selected_user.role or "user") != "admin" else 1,
                    )
                    new_is_admin = st.checkbox(
                        "Администратор", value=selected_user.is_admin
                    )
                    new_is_active = st.checkbox(
                        "Активен", value=selected_user.is_active
                    )
                    # Удалять пользователей может только root и только не-ROOT
                    can_delete = current_is_root

                delete_user_flag = False
                if can_delete:
                    delete_user_flag = st.checkbox(
                        "Удалить этого пользователя безвозвратно", value=False
                    )

                submitted = st.form_submit_button("Сохранить изменения")
                if submitted:
                    db2 = _get_db_session()
                    try:
                        if can_delete and delete_user_flag:
                            auth_core.delete_user(db2, selected_user.id)
                            st.success("Пользователь удалён.")
                        else:
                            auth_core.update_user(
                                db2,
                                user_id=selected_user.id,
                                email=new_email
                                if new_email != selected_user.email
                                else None,
                                name=new_name
                                if new_name != selected_user.name
                                else None,
                                password=new_password or None,
                                phone=new_phone
                                if new_phone != (selected_user.phone or "")
                                else None,
                                role=new_role
                                if (new_role is not None)
                                and new_role != (selected_user.role or "user")
                                else None,
                                is_admin=new_is_admin
                                if (new_is_admin is not None)
                                and new_is_admin != selected_user.is_admin
                                else None,
                                is_active=new_is_active
                                if (new_is_active is not None)
                                and new_is_active != selected_user.is_active
                                else None,
                            )
                            st.success("Изменения сохранены.")
                    except ValueError as exc:
                        st.error(str(exc))
                    except Exception as exc:  # pragma: no cover
                        st.error(f"Ошибка при обновлении пользователя: {exc}")
                    finally:
                        db2.close()
                    st.rerun()

    # Создание нового пользователя
    with col_right:
        st.markdown("**Добавить пользователя**")
        with st.form("create_user_form_admin"):
            email = st.text_input("Email")
            name = st.text_input("Имя")
            password = st.text_input("Пароль", type="password")
            password_confirm = st.text_input(
                "Повторите пароль", type="password"
            )
            phone = st.text_input("Телефон (опционально)")
            # Новых ROOT-пользователей создавать нельзя
            role = st.selectbox("Роль", options=["user", "admin"], index=0)
            is_admin = st.checkbox("Администратор", value=False)

            submitted_new = st.form_submit_button("Создать пользователя")
            if submitted_new:
                if not email or not name or not password or not password_confirm:
                    st.error("Email, имя и оба поля пароля обязательны.")
                elif password != password_confirm:
                    st.error("Пароли в обоих полях должны совпадать.")
                else:
                    db3 = _get_db_session()
                    try:
                        auth_core.register_user(
                            db=db3,
                            email=email,
                            name=name,
                            password=password,
                            phone=phone or None,
                            role=role,
                            is_admin=is_admin,
                            is_active=True,
                        )
                        st.success("Пользователь создан.")
                    except ValueError as exc:
                        st.error(str(exc))
                    except Exception as exc:  # pragma: no cover
                        st.error(f"Ошибка при создании пользователя: {exc}")
                    finally:
                        db3.close()
                    st.rerun()


