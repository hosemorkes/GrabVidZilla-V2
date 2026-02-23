"""
Графический интерфейс для GrabVidZilla на Streamlit.

Функционал:
- Ввод URL видео
- Кнопка "Analysis": извлечение информации о ролике, список качеств и языков субтитров
- Кнопка "Download": загрузка выбранного формата в фоновом режиме с прогрессом
- После завершения доступна кнопка скачивания файла в браузере (и файл сохраняется в папку Downloads)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

import sys
import time
import requests
import streamlit as st

from ui import ui_auth

# URL API по умолчанию
API_BASE_URL = "http://localhost:8000"


def _get_default_downloads_dir() -> Path:
    """
    Возвращает путь к стандартной папке загрузок пользователя.
    Кроссплатформенно: $HOME/Downloads (Windows/Linux/macOS).
    """
    home = Path.home()
    downloads = home / "Downloads"
    try:
        downloads.mkdir(parents=True, exist_ok=True)
    except Exception:
        # В редких случаях без прав — откатимся к текущей папке
        return Path(".")
    return downloads


def _shutdown_server() -> None:
    """
    Останавливает процесс Streamlit (как Ctrl+C).
    """
    import os as _os
    import signal as _signal
    try:
        if hasattr(_signal, "SIGINT"):
            _os.kill(_os.getpid(), _signal.SIGINT)
        if hasattr(_signal, "SIGTERM"):
            _os.kill(_os.getpid(), _signal.SIGTERM)
    except Exception:
        pass
    finally:
        _os._exit(0)

def _build_format_selector(selected_quality: str) -> str:
    """
    Возвращает строку формата для yt-dlp на основе выбранного качества.
    - 'audio only' -> 'bestaudio/best'
    - '<Xp>' -> 'bv*[height<=X]+ba/best[height<=X]'
    """
    if selected_quality == "audio only":
        return "bestaudio/best"
    try:
        h = int(selected_quality.replace("p", "").strip())
        return f"bv*[height<={h}]+ba/best[height<={h}]"
    except Exception:
        # Фолбэк — лучшая связка
        return "bv*+ba/best"


def _format_human_size(num_bytes: float | int | None) -> str:
    """
    Человекочитаемый размер в Б/КБ/МБ/ГБ.
    """
    if not num_bytes or num_bytes <= 0:
        return "—"
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    size = float(num_bytes)
    idx = 0
    while size >= 1024.0 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    return f"{size:.1f} {units[idx]}"


def _format_human_speed(bytes_per_sec: float | None) -> str:
    if not bytes_per_sec or bytes_per_sec <= 0:
        return "—"
    return f"{_format_human_size(bytes_per_sec)}/с"

def _format_lang_label(lang_code: str) -> str:
    """
    Возвращает человекопонятное имя языка по коду (ru, en, en-US и т.п.).
    """
    mapping = {
        "ru": "Русский",
        "en": "Английский",
        "uk": "Украинский",
        "be": "Белорусский",
        "de": "Немецкий",
        "fr": "Французский",
        "es": "Испанский",
        "pt": "Португальский",
        "it": "Итальянский",
        "pl": "Польский",
        "tr": "Турецкий",
        "ar": "Арабский",
        "hi": "Хинди",
        "id": "Индонезийский",
        "vi": "Вьетнамский",
        "th": "Тайский",
        "zh": "Китайский",
        "ja": "Японский",
        "ko": "Корейский",
        "fa": "Персидский",
        "he": "Иврит",
        "nl": "Нидерландский",
        "sv": "Шведский",
        "no": "Норвежский",
        "da": "Датский",
        "fi": "Финский",
        "cs": "Чешский",
        "sk": "Словацкий",
        "sl": "Словенский",
        "ro": "Румынский",
        "hu": "Венгерский",
        "bg": "Болгарский",
        "sr": "Сербский",
        "hr": "Хорватский",
        "el": "Греческий",
        "et": "Эстонский",
        "lv": "Латышский",
        "lt": "Литовский",
        "kk": "Казахский",
        "uz": "Узбекский",
        "ka": "Грузинский",
        "az": "Азербайджанский",
    }
    if not isinstance(lang_code, str) or not lang_code:
        return "Неизвестный"
    code = lang_code.lower()
    base = code
    region = None
    if "-" in code or "_" in code:
        sep = "-" if "-" in code else "_"
        parts = code.split(sep, 1)
        base = parts[0]
        region = parts[1].upper()
    name = mapping.get(base, base)
    return f"{name} ({region})" if region else name


def _init_session_state() -> None:
    """
    Гарантирует наличие необходимых ключей в session_state.
    """
    defaults = {
        "url": "",
        "analyzed": False,
        "info": None,
        "qualities": [],
        "subtitle_langs": [],
        "selected_quality": None,
        "selected_subtitle": None,
        "last_download_path": None,
        "current_task_id": None,
        "download_progress": 0.0,
        "media_search_url": "",
        "media_search_results": None,
        "media_search_error": None,
        "media_search_use_browser": True,
        "media_search_proxy_server": "",
        "media_search_proxy_username": "",
        "media_search_proxy_password": "",
        "media_search_translations": [],
        "media_search_selected_translation_index": None,
        "media_search_selected_translation_hash": None,
        "media_search_selected_translation_name": None,
        "media_search_streams": [],
        "media_search_selected_quality_index": None,
        "media_search_selected_quality_label": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def main() -> None:
    """
    Точка входа Streamlit-приложения.
    """
    st.set_page_config(page_title="GrabVidZilla", page_icon="🎬", layout="centered", initial_sidebar_state="collapsed")
    _init_session_state()

    # Блок аутентификации (логин/регистрация/выход)
    ui_auth.render_auth_block()
    ui_auth.require_login()

    # Профильное меню в верхнем левом углу:
    # - компактный индикатор профиля (👤 user);
    # - при наличии прав admin — иконка ⚙ с панелью управления пользователями.
    user_info = getattr(st.session_state, "current_user", None) or st.session_state.get("current_user")
    if user_info:
        header_cols = st.columns([2, 3])
        with header_cols[0]:
            profile_cols = st.columns([1, 1])
            # Неброский профильный индикатор: при клике раскрывается поповер
            with profile_cols[0]:
                with st.popover(f"👤 {user_info.get('name', '')}", use_container_width=False):
                    st.markdown(
                        f"**Пользователь:** {user_info.get('name', '')}\n\n"
                        f"`{user_info.get('email', '')}`"
                    )
                    if st.button("Выйти", key="header_logout_btn"):
                        ui_auth.logout()
                        st.rerun()
            # Иконка настроек только для администраторов
            if user_info.get("is_admin"):
                with profile_cols[1]:
                    with st.popover("⚙️", use_container_width=True):
                        ui_auth.render_admin_panel()
        with header_cols[1]:
            pass  # справа оставляем место под заголовок/логотип

    # Внутренние стили для оформления экрана под макет
    st.markdown(
        """
        <style>
        /* Подключение шрифта Work Sans */
        @import url('https://fonts.googleapis.com/css2?family=Work+Sans:wght@600;700;800&display=swap');

        /* Глобально применяем Work Sans ко всем основным контейнерам и виджетам,
           но не трогаем иконки (Material Icons), чтобы не появлялись тексты
           вроде 'keyboard_arrow_right'. */
        :root, html, body, .stApp, .main .block-container,
        [data-testid="stMarkdownContainer"],
        [data-testid="stWidgetLabel"],
        .stText, .stCaption, .stAlertContainer,
        .stButton > button,
        .stTextInput > div > div > input,
        .stSelectbox, .stSelectbox div, .stSelectbox label {
            font-family: 'Work Sans', system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', 'Liberation Sans', sans-serif !important;
        }

        /* Фон приложения */
        .stApp {
            background: radial-gradient(1200px 600px at 20% -10%, #0b2b28 0%, #071b19 45%, #041312 80%);
        }
        /* Контейнер контента */
        .main .block-container {
            padding-top: 1.8rem;
            padding-bottom: 2rem;
            max-width: 1176px; /* +20% ширины от 980px */
            container-type: inline-size; /* для корректной работы cqi */
        }
        /* Карточка-рамка вокруг основного блока */
        .gvz-card {
            border: 1px solid rgba(55, 189, 142, 0.25);
            border-radius: 14px;
            background: rgba(6, 27, 25, 0.55);
            box-shadow: 0 0 0 1px rgba(55,189,142,0.05) inset, 0 20px 40px rgba(0,0,0,0.35);
            padding: 18px 18px 28px;
            /* Включаем контейнерные единицы для адаптивной типографики внутри карточки */
            container-type: inline-size;
        }
        /* Заголовок */
        .gvz-title {
            font-family: 'Work Sans', system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', 'Liberation Sans', sans-serif;
            /* Адаптивный размер: чуть меньший максимум и чувствительнее к ширине контейнера */
            font-size: clamp(22px, 4.0cqi, 52px);
            line-height: 1.12;
            font-weight: 700;
            letter-spacing: 0.1px;
            color: #e9fff7;
            text-shadow: 0 2px 12px rgba(24, 180, 120, 0.25);
            margin: 8px 0 18px 0;
            white-space: nowrap;
            text-align: center;
        }
        /* Поле ввода */
        .stTextInput > div > div > input {
            background: #172523;
            color: #e8fff7;
            border: 1px solid rgba(55, 189, 142, 0.25);
            border-radius: 10px;
            height: 42px;
        }
        /* Кнопки */
        .stButton > button {
            background: #1faa89;
            color: #06201c;
            border: 1px solid rgba(55,189,142,0.35);
            border-radius: 10px;
            height: 42px;
            font-weight: 700;
        }
        .stButton > button:hover {
            background: #24be98;
            border-color: rgba(55,189,142,0.55);
        }
        .stButton > button:disabled {
            background: #0f3a34 !important;
            border-color: rgba(55,189,142,0.15) !important;
            color: #6aa99a !important;
        }
        /* Вторичная кнопка (Analysis) — чуть темнее */
        .gvz-secondary .stButton > button {
            background: #0f7e64;
            color: #e9fff7;
        }
        .gvz-secondary .stButton > button:hover {
            background: #129476;
        }
        /* Центровка нижней кнопки */
        .gvz-center {
            display: flex;
            justify-content: center;
        }
        /* Скрыть label у поля ввода URL */
        .gvz-url [data-testid="stWidgetLabel"], .gvz-url label { 
            display: none !important; 
        }
        /* Логотип в едином фоне */
        .gvz-logo-wrap {
            display: flex;
            justify-content: center;
            margin-bottom: 12px;
        }
        .gvz-logo-wrap img {
            background: #061b19;
            border: 1px solid rgba(55,189,142,0.18);
            border-radius: 14px;
            padding: 12px;
            box-shadow: 0 0 0 1px rgba(55,189,142,0.06) inset, 0 10px 24px rgba(0,0,0,0.28);
        }
        /* Боковая панель */
        section[data-testid="stSidebar"] { display: none !important; }
        div[data-testid="collapsedControl"] { display: none !important; }
        /* Убираем кнопку/панель Deploy/Toolbar в шапке */
        div[data-testid="stToolbar"],
        [data-testid="stToolbar"],
        header [data-testid="stToolbar"],
        .stAppToolbar,
        button[data-testid="stBaseButton-header"],
        button[data-testid="stBaseButton-headerNoPadding"] {
            display: none !important;
            visibility: hidden !important;
            height: 0 !important;
            padding: 0 !important;
            margin: 0 !important;
        }
        #MainMenu { visibility: hidden; }
        header { height: 0px; visibility: hidden; }

        /* главный контейнер */
        [data-testid="stMainBlockContainer"] {
            padding-top: 0.4rem; /* или 0 */
        }
        /* убрать возможные внешние отступы у первого блока */
        [data-testid="stMainBlockContainer"] > :first-child {
            margin-top: 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Логотип вместо карточки
    logo_path = Path(__file__).parent / "grabvidzilla-logo.png"
    if logo_path.exists():
        _lc = st.columns([1, 2, 1])
        with _lc[1]:
            st.markdown('<div class="gvz-logo-wrap">', unsafe_allow_html=True)
            st.image(str(logo_path), width="content")
            st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="gvz-title">Download your favorite videos</div>', unsafe_allow_html=True)

    # URL ввода и кнопка Analysis ниже поля
    st.markdown('<div class="gvz-url">', unsafe_allow_html=True)
    url = st.text_input(
        "Video URL",
        value=st.session_state["url"],
        placeholder="Enter video URL...",
        label_visibility="collapsed",
    )
    st.markdown("</div>", unsafe_allow_html=True)

    # Загрузка cookies при необходимости
    tools_dir = (Path(__file__).resolve().parents[1] / "tools")
    tools_dir.mkdir(parents=True, exist_ok=True)
    with st.expander("Advanced (cookies)", expanded=False):
        uploaded = st.file_uploader("Cookies (Netscape)", type=["txt"], accept_multiple_files=False)
        if uploaded is not None:
            target = tools_dir / "cookies.txt"
            target.write_bytes(uploaded.getbuffer())
            st.session_state["cookies_path"] = str(target)
            st.success(f"Cookies сохранены: {target}")
        else:
            # если файл уже есть — используем его автоматически
            default_cookies = tools_dir / "cookies.txt"
            if default_cookies.exists():
                st.session_state["cookies_path"] = str(default_cookies)

        col_a1, col_a2 = st.columns(2)
        with col_a2:
            if st.button("Stop server", help="Остановить Streamlit (как Ctrl+C)"):
                _shutdown_server()

    # Secondary-styled Analysis (под полем ввода)
    st.markdown('<div class="gvz-secondary">', unsafe_allow_html=True)
    analyze_clicked = st.button("Analysis", width="stretch")
    st.markdown("</div>", unsafe_allow_html=True)

    st.session_state["url"] = url.strip()

    # (карточка удалена — закрывающий тег не требуется)
    download_clicked = False

    # Блок анализа
    if analyze_clicked:
        if not url:
            st.error("Введите URL.")
        else:
            with st.spinner("Анализ видео..."):
                try:
                    cookies_path = st.session_state.get("cookies_path")
                    params = {"url": url}
                    if cookies_path:
                        params["cookies_path"] = cookies_path
                    response = requests.get(f"{API_BASE_URL}/analyze", params=params, timeout=30)
                    response.raise_for_status()
                    data = response.json()
                    info = data["info"]
                    qualities = data["qualities"]
                    subtitle_langs = data["subtitle_langs"]
                    st.session_state["analyzed"] = True
                    st.session_state["info"] = info
                    st.session_state["qualities"] = qualities
                    st.session_state["subtitle_langs"] = subtitle_langs
                    # Значения по умолчанию
                    st.session_state["selected_quality"] = qualities[0] if qualities else "best"
                    st.session_state["selected_subtitle"] = subtitle_langs[0] if subtitle_langs else None
                    st.success("Анализ завершён.")
                except requests.exceptions.RequestException as e:
                    st.session_state["analyzed"] = False
                    error_msg = str(e)
                    if hasattr(e, "response") and e.response is not None:
                        try:
                            detail = e.response.json().get("detail", error_msg)
                            error_msg = detail
                        except Exception:
                            error_msg = f"HTTP {e.response.status_code}: {error_msg}"
                    st.error(f"Не удалось проанализировать URL: {error_msg}")
                except Exception as e:
                    st.session_state["analyzed"] = False
                    st.error(f"Не удалось проанализировать URL: {e}")

    # Панель выбора параметров после анализа
    if st.session_state.get("analyzed"):
        info = st.session_state.get("info") or {}
        title = info.get("title") or "Видео"
        duration = info.get("duration")
        thumbnail = info.get("thumbnail")

        with st.container(border=True):
            st.subheader(title)
            meta_cols = st.columns([1, 1, 2])
            with meta_cols[0]:
                if duration:
                    m, s = divmod(int(duration), 60)
                    st.caption(f"Длительность: {m}м {s}с")
            with meta_cols[1]:
                if info.get("uploader"):
                    st.caption(f"Автор: {info.get('uploader')}")
            with meta_cols[2]:
                cap = []
                if info.get("webpage_url_domain"):
                    cap.append(f"Источник: {info.get('webpage_url_domain')}")
                if info.get("gvz_used_client"):
                    cap.append(f"client: {info.get('gvz_used_client')}")
                if cap:
                    st.caption(" | ".join(cap))
            if thumbnail:
                st.image(thumbnail, width="stretch")

        with st.container(border=True):
            st.subheader("Параметры загрузки")
            st.session_state["selected_quality"] = st.selectbox(
                "Качество",
                options=st.session_state.get("qualities") or ["best"],
                index=0,
            )

            subtitle_lang = None
            if st.session_state.get("subtitle_langs"):
                codes = st.session_state["subtitle_langs"]
                options = ["__none__"] + codes
                subtitle_choice = st.selectbox(
                    "Субтитры (необязательно)",
                    options=options,
                    index=0,
                    format_func=lambda opt: "Без субтитров" if opt == "__none__" else _format_lang_label(opt),
                )
                subtitle_lang = None if subtitle_choice == "__none__" else subtitle_choice
            st.session_state["selected_subtitle"] = subtitle_lang

        # Кнопка загрузки (после параметров)
        download_clicked = st.button("Download", width="stretch")

    # Кнопка загрузки
    if download_clicked:
        selected_quality: str = st.session_state.get("selected_quality") or "best"
        # Прежняя стратегия: гибкая строка формата по выбранному качеству
        fmt = _build_format_selector(selected_quality)

        try:
            # Отправляем запрос на скачивание через API
            payload = {
                "url": st.session_state["url"],
                "format": fmt if selected_quality != "audio only" else None,
                "audio_only": (selected_quality == "audio only"),
            }
            cookies_path = st.session_state.get("cookies_path")
            if cookies_path:
                payload["cookies_path"] = cookies_path
            subtitle_lang = st.session_state.get("selected_subtitle")
            if subtitle_lang:
                payload["subtitle_lang"] = subtitle_lang

            response = requests.post(f"{API_BASE_URL}/downloads", json=payload, timeout=10)
            response.raise_for_status()
            task_data = response.json()
            task_id = task_data["id"]
            st.session_state["current_task_id"] = task_id
            st.success(f"Загрузка начата (ID: {task_id[:8]}...)")
        except requests.exceptions.RequestException as e:
            error_msg = str(e)
            if hasattr(e, "response") and e.response is not None:
                try:
                    detail = e.response.json().get("detail", error_msg)
                    error_msg = detail
                except Exception:
                    error_msg = f"HTTP {e.response.status_code}: {error_msg}"
            st.error(f"Ошибка запуска загрузки: {error_msg}")

    # Отслеживание прогресса загрузки
    if st.session_state.get("current_task_id"):
        task_id = st.session_state["current_task_id"]
        try:
            response = requests.get(f"{API_BASE_URL}/downloads/{task_id}", timeout=5)
            response.raise_for_status()
            task_status = response.json()

            state = task_status.get("state", "unknown")
            progress_percent = task_status.get("progress_percent", 0.0)
            filename = task_status.get("filename")
            error = task_status.get("error")

            # Прогресс-бар
            progress_bar = st.progress(
                int(progress_percent),
                text=f"Загрузка: {progress_percent:.1f}% ({state})"
            )

            # Детали прогресса
            status_cols = st.columns(3)
            with status_cols[0]:
                downloaded = task_status.get("bytes_downloaded")
                total = task_status.get("total_bytes")
                st.info(f"{_format_human_size(downloaded)} / {_format_human_size(total)}")
            with status_cols[1]:
                speed = task_status.get("speed_bps")
                st.caption(f"Скорость: {_format_human_speed(speed)}")
            with status_cols[2]:
                eta = task_status.get("eta_s")
                if eta:
                    st.caption(f"Осталось: {eta:.0f} сек")

            if state == "completed":
                progress_bar.progress(100, text="Готово ✅")
                st.success(f"Файл загружен: {filename}")
                checksum_hex = task_status.get("checksum_sha256")
                if checksum_hex:
                    st.caption(f"SHA-256: `{checksum_hex}`")
                st.session_state["last_download_path"] = filename
                st.session_state["current_task_id"] = None

                # Кнопка скачать через браузер
                try:
                    file_response = requests.get(
                        f"{API_BASE_URL}/downloads/{task_id}/file",
                        timeout=30,
                        stream=True
                    )
                    file_response.raise_for_status()
                    st.download_button(
                        label="Скачать файл в браузере",
                        data=file_response.content,
                        file_name=filename or "video",
                        mime="application/octet-stream",
                    )
                except Exception as e:
                    st.warning(f"Не удалось подготовить файл для скачивания: {e}")

            elif state == "failed":
                progress_bar.progress(0, text="Ошибка ❌")
                st.error(f"Ошибка загрузки: {error or 'Неизвестная ошибка'}")
                st.session_state["current_task_id"] = None

            elif state in ("queued", "running"):
                # Автоматически обновляем страницу для обновления прогресса
                time.sleep(0.5)
                st.rerun()

            elif state == "cancelled":
                st.warning("Загрузка отменена")
                st.session_state["current_task_id"] = None

        except requests.exceptions.RequestException as e:
            st.error(f"Ошибка получения статуса загрузки: {e}")
            st.session_state["current_task_id"] = None

    st.divider()

    with st.expander("🔍 Найти медиа-ссылки на странице", expanded=False):
        media_url = st.text_input(
            "URL страницы",
            key="media_search_url",
            placeholder="https://example.com/page",
        )
        use_browser_on_empty = st.checkbox(
            "Использовать Playwright, если статический парсер ничего не нашёл",
            key="media_search_use_browser",
        )

        proxy_cols = st.columns(3)
        with proxy_cols[0]:
            proxy_server = st.text_input(
                "Прокси сервер",
                key="media_search_proxy_server",
                placeholder="http://host:3128",
                help="Оставьте пустым, чтобы использовать значения из переменных окружения API.",
            )
        with proxy_cols[1]:
            proxy_username = st.text_input(
                "Прокси логин",
                key="media_search_proxy_username",
                placeholder="user",
            )
        with proxy_cols[2]:
            proxy_password = st.text_input(
                "Прокси пароль",
                key="media_search_proxy_password",
                type="password",
            )

        if st.button("Искать медиа-ссылки", key="media_search_button"):
            if not media_url.strip():
                st.warning("Введите URL страницы для поиска.")
            else:
                with st.spinner("Ищем ссылки..."):
                    params = {
                        "url": media_url.strip(),
                        "use_browser": "false",
                        "fallback_to_browser": "false",
                    }
                    cookies_path = st.session_state.get("cookies_path")
                    if cookies_path:
                        params["cookies_path"] = cookies_path

                    translations = st.session_state.get("media_search_translations") or []
                    selected_idx = st.session_state.get("media_search_selected_translation_index")
                    translation_hash = None
                    translation_name = None
                    if translations and selected_idx is not None and 0 <= selected_idx < len(translations):
                        translation_hash = translations[selected_idx].get("hash")
                        translation_name = translations[selected_idx].get("name")
                        if translation_hash:
                            params["translation_hash"] = translation_hash
                    st.session_state["media_search_selected_translation_hash"] = translation_hash
                    st.session_state["media_search_selected_translation_name"] = translation_name

                    try:
                        response = requests.get(f"{API_BASE_URL}/media", params=params, timeout=30)
                        response.raise_for_status()
                        data = response.json()
                        streams = data.get("hls_streams", [])
                        st.session_state["media_search_streams"] = streams
                        quality_labels: list[str] = []
                        for stream in streams:
                            label = (stream.get("quality") or "").strip() or "Неизвестно"
                            if label not in quality_labels:
                                quality_labels.append(label)
                        previous_quality_label = st.session_state.get("media_search_selected_quality_label")
                        if quality_labels:
                            if previous_quality_label in quality_labels:
                                selected_quality_label = previous_quality_label
                            else:
                                selected_quality_label = quality_labels[0]
                            st.session_state["media_search_selected_quality_label"] = selected_quality_label
                            st.session_state["media_search_selected_quality_index"] = quality_labels.index(selected_quality_label)
                        else:
                            selected_quality_label = None
                            st.session_state["media_search_selected_quality_label"] = None
                            st.session_state["media_search_selected_quality_index"] = None

                        if streams and st.session_state.get("media_search_selected_quality_label"):
                            selected_quality_label = st.session_state["media_search_selected_quality_label"]
                            filtered_streams = [
                                stream for stream in streams
                                if ((stream.get("quality") or "").strip() or "Неизвестно") == selected_quality_label
                            ]
                            if filtered_streams:
                                hls_urls = [stream.get("url") for stream in filtered_streams if stream.get("url")]
                                hls_urls = [u for u in hls_urls if u]
                        else:
                            st.session_state["media_search_selected_quality_label"] = selected_quality_label

                        new_translations = data.get("translations") or []
                        st.session_state["media_search_translations"] = new_translations
                        if new_translations:
                            if translation_hash:
                                for idx, item in enumerate(new_translations):
                                    if item.get("hash") == translation_hash:
                                        st.session_state["media_search_selected_translation_index"] = idx
                                        break
                            elif st.session_state.get("media_search_selected_translation_index") is None:
                                st.session_state["media_search_selected_translation_index"] = 0
                            idx = st.session_state.get("media_search_selected_translation_index")
                            if idx is not None and 0 <= idx < len(new_translations):
                                st.session_state["media_search_selected_translation_hash"] = new_translations[idx].get("hash")
                                st.session_state["media_search_selected_translation_name"] = new_translations[idx].get("name")
                        else:
                            st.session_state["media_search_selected_translation_index"] = None
                            st.session_state["media_search_selected_translation_hash"] = None
                            st.session_state["media_search_selected_translation_name"] = None

                        if not hls_urls and not file_urls and use_browser_on_empty:
                            params_browser = {
                                "url": media_url.strip(),
                                "use_browser": "true",
                                "fallback_to_browser": "false",
                            }
                            if cookies_path:
                                params_browser["cookies_path"] = cookies_path
                            if translation_hash:
                                params_browser["translation_hash"] = translation_hash
                            if proxy_server.strip():
                                params_browser["proxy_server"] = proxy_server.strip()
                            if proxy_username.strip():
                                params_browser["proxy_username"] = proxy_username.strip()
                            if proxy_password.strip():
                                params_browser["proxy_password"] = proxy_password.strip()

                            response_browser = requests.get(
                                f"{API_BASE_URL}/media",
                                params=params_browser,
                                timeout=60,
                            )
                            response_browser.raise_for_status()
                            data_browser = response_browser.json()
                            hls_urls = data_browser.get("hls_urls", [])
                            file_urls = data_browser.get("file_urls", [])
                            used_browser = data_browser.get("used_browser", True)
                            page_title = data_browser.get("page_title") or page_title
                            new_translations = data_browser.get("translations") or new_translations
                            browser_streams = data_browser.get("hls_streams", [])
                            st.session_state["media_search_streams"] = browser_streams or st.session_state.get("media_search_streams", [])
                            quality_labels = []
                            for stream in st.session_state["media_search_streams"]:
                                label = (stream.get("quality") or "").strip() or "Неизвестно"
                                if label not in quality_labels:
                                    quality_labels.append(label)
                            previous_quality_label = st.session_state.get("media_search_selected_quality_label")
                            if quality_labels:
                                if previous_quality_label in quality_labels:
                                    selected_quality_label = previous_quality_label
                                else:
                                    selected_quality_label = quality_labels[0]
                                st.session_state["media_search_selected_quality_label"] = selected_quality_label
                                st.session_state["media_search_selected_quality_index"] = quality_labels.index(selected_quality_label)
                                filtered_streams = [
                                    stream for stream in st.session_state["media_search_streams"]
                                    if ((stream.get("quality") or "").strip() or "Неизвестно") == selected_quality_label
                                ]
                                if filtered_streams:
                                    hls_urls = [stream.get("url") for stream in filtered_streams if stream.get("url")]
                                    hls_urls = [u for u in hls_urls if u]
                            else:
                                st.session_state["media_search_selected_quality_label"] = None
                                st.session_state["media_search_selected_quality_index"] = None

                        st.session_state["media_search_results"] = {
                            "hls": hls_urls,
                            "files": file_urls,
                            "used_browser": used_browser,
                            "title": page_title,
                            "translation": st.session_state.get("media_search_selected_translation_name"),
                            "quality": st.session_state.get("media_search_selected_quality_label"),
                            "streams": st.session_state.get("media_search_streams") or [],
                        }
                        st.session_state["media_search_error"] = None
                    except requests.exceptions.RequestException as exc:
                        st.session_state["media_search_error"] = str(exc)
                        st.session_state["media_search_results"] = None

        if st.session_state.get("media_search_error"):
            st.error(st.session_state["media_search_error"])

        results = st.session_state.get("media_search_results")
        if results:
            if results.get("used_browser"):
                st.info("Ссылки найдены с помощью Playwright.")
            if results.get("title"):
                st.subheader(results["title"])
            if results.get("translation"):
                st.caption(f"Перевод: {results['translation']}")
            if results.get("quality"):
                st.caption(f"Качество: {results['quality']}")

            if results.get("hls"):
                st.subheader("HLS (m3u8)")
                for item in results["hls"]:
                    st.code(item, language="")
            else:
                st.caption("HLS ссылки не найдены.")

            if results.get("files"):
                st.subheader("Файловые ссылки")
                for item in results["files"]:
                    st.code(item, language="")
            else:
                st.caption("Прямые ссылки на файлы не найдены.")

        elif st.session_state.get("media_search_error") is None:
            st.caption("Результаты появятся после поиска.")

        translations = st.session_state.get("media_search_translations") or []
        if translations:
            names = [item.get("name") or f"Перевод {idx+1}" for idx, item in enumerate(translations)]
            current_idx = st.session_state.get("media_search_selected_translation_index")
            if current_idx is None or not (0 <= current_idx < len(names)):
                current_idx = 0
            selected_idx = st.selectbox(
                "Перевод",
                options=list(range(len(names))),
                format_func=lambda i: names[i],
                index=current_idx,
            )
            st.session_state["media_search_selected_translation_index"] = selected_idx
            st.session_state["media_search_selected_translation_hash"] = translations[selected_idx].get("hash")
            st.session_state["media_search_selected_translation_name"] = translations[selected_idx].get("name")
        else:
            st.session_state["media_search_selected_translation_index"] = None
            st.session_state["media_search_selected_translation_hash"] = None
            st.session_state["media_search_selected_translation_name"] = None

        streams = st.session_state.get("media_search_streams") or []
        selected_quality_label = st.session_state.get("media_search_selected_quality_label")

        if st.session_state.get("media_search_error"):
            st.error(st.session_state["media_search_error"])

        results = st.session_state.get("media_search_results")
        if results:
            if streams:
                target_label = (selected_quality_label or "").strip() or None
                def _normalize_quality(label: Optional[str]) -> str:
                    return (label or "").strip() or "Неизвестно"
                if target_label:
                    filtered_streams = [
                        stream for stream in streams
                        if _normalize_quality(stream.get("quality")) == target_label
                    ]
                else:
                    filtered_streams = streams
                if filtered_streams:
                    results["hls"] = [stream.get("url") for stream in filtered_streams if stream.get("url")]
                else:
                    results["hls"] = [stream.get("url") for stream in streams if stream.get("url")]
                results["quality"] = selected_quality_label

        streams = st.session_state.get("media_search_streams") or []
        if streams:
            quality_labels = []
            for stream in streams:
                label = (stream.get("quality") or "").strip() or "Неизвестно"
                if label not in quality_labels:
                    quality_labels.append(label)
            current_quality_idx = st.session_state.get("media_search_selected_quality_index")
            if current_quality_idx is None or not (0 <= current_quality_idx < len(quality_labels)):
                current_quality_idx = 0
            selected_quality_idx = st.selectbox(
                "Качество",
                options=list(range(len(quality_labels))),
                format_func=lambda i: quality_labels[i],
                index=current_quality_idx,
            )
            st.session_state["media_search_selected_quality_index"] = selected_quality_idx
            st.session_state["media_search_selected_quality_label"] = quality_labels[selected_quality_idx]
        else:
            st.session_state["media_search_selected_quality_index"] = None
            st.session_state["media_search_selected_quality_label"] = None


if __name__ == "__main__":
    main()

