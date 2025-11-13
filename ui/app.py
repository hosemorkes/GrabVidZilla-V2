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
from typing import Any, Dict, List, Tuple

import sys
import streamlit as st

# Бизнес-логика — используем только ядро
# Добавим корень проекта в sys.path, чтобы импортировать пакет core при запуске через Streamlit
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.downloader import download_video, analyze_video


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

    # Внутренние стили для оформления экрана под макет
    st.markdown(
        """
        <style>
        /* Подключение шрифта Work Sans */
        @import url('https://fonts.googleapis.com/css2?family=Work+Sans:wght@600;700;800&display=swap');

        /* Глобально применяем Work Sans ко всем основным контейнерам и виджетам */
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
        div[data-testid="stToolbar"] { display: none !important; }
        header [data-testid="stToolbar"] { display: none !important; }
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
                    info, qualities, subtitle_langs = analyze_video(url, cookies_path=st.session_state.get("cookies_path"))
                    st.session_state["analyzed"] = True
                    st.session_state["info"] = info
                    st.session_state["qualities"] = qualities
                    st.session_state["subtitle_langs"] = subtitle_langs
                    # Значения по умолчанию
                    st.session_state["selected_quality"] = qualities[0] if qualities else "best"
                    st.session_state["selected_subtitle"] = subtitle_langs[0] if subtitle_langs else None
                    st.success("Анализ завершён.")
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

        # Папка загрузок пользователя
        downloads_dir = _get_default_downloads_dir()

        # Прогресс-бар и текстовые индикаторы
        progress_bar = st.progress(0, text="Начало загрузки...")
        status_placeholder = st.empty()
        speed_placeholder = st.empty()

        def on_progress(percent: float) -> None:
            progress_bar.progress(int(percent), text=f"Загрузка: {percent:.1f}%")

        def on_progress_info(info: dict) -> None:
            downloaded = info.get("downloaded_bytes")
            total = info.get("total_bytes")
            spd = info.get("speed")
            status_placeholder.info(
                f"{_format_human_size(downloaded)} из {_format_human_size(total)}"
            )
            speed_placeholder.caption(f"Скорость: {_format_human_speed(spd)}")

        try:
            with st.spinner("Загружаем файл..."):
                try:
                    filepath = download_video(
                        url=st.session_state["url"],
                        output_path=str(downloads_dir),
                        progress_callback=on_progress,
                        progress_info_callback=on_progress_info,
                        cookies_path=st.session_state.get("cookies_path"),
                        format=fmt,
                        audio_only=(selected_quality == "audio only"),
                        subtitle_lang=st.session_state.get("selected_subtitle"),
                    )
                except Exception as e:
                    # Фолбэк, если запрошенный формат недоступен — возьмём best
                    msg = str(e).lower()
                    if "requested format is not available" in msg or "no such format" in msg:
                        filepath = download_video(
                            url=st.session_state["url"],
                            output_path=str(downloads_dir),
                            progress_callback=on_progress,
                            progress_info_callback=on_progress_info,
                            cookies_path=st.session_state.get("cookies_path"),
                            format="best",
                            audio_only=False,
                            subtitle_lang=st.session_state.get("selected_subtitle"),
                        )
                    else:
                        raise
            st.session_state["last_download_path"] = filepath
            progress_bar.progress(100, text="Готово ✅")

            st.success(f"Файл сохранён: {filepath}")

            # Кнопка скачать через браузер
            try:
                file_path = Path(filepath)
                if file_path.exists():
                    with file_path.open("rb") as f:
                        st.download_button(
                            label="Скачать файл в браузере",
                            data=f,
                            file_name=file_path.name,
                            mime="application/octet-stream",
                        )
                else:
                    st.warning("Не удалось найти файл для скачивания в браузере.")
            except Exception:
                st.warning("Не удалось подготовить файл для скачивания в браузере.")

        except Exception as e:
            st.error(f"Ошибка загрузки: {e}")


if __name__ == "__main__":
    main()

