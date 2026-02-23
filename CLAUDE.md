# GrabVidZilla — Project Rules

## Overview

**Цель:** кроссплатформенный инструмент (Windows/Linux) для скачивания видео (YouTube, VK, Instagram, TikTok и др.) с двумя интерфейсами: CLI и браузероподобный десктоп-UI (Streamlit).

**Технологии:**
- Python 3.11+
- yt-dlp (скачивание), обязательен ffmpeg в системе
- CLI: click + rich (цвета, эмодзи, прогресс)
- UI: Streamlit
- REST API: FastAPI + Uvicorn (эндпоинты: /health, /formats, /downloads, /media, /analyze)
- SQLite + SQLAlchemy: БД для пользователей (`data/app.db`)
- Docker / Docker Compose для одинаковой среды
- requests + BeautifulSoup4: парсинг HTML-страниц и поиск ссылок на HLS (m3u8) и видеофайлы
- Playwright (Chromium headless): динамический парсинг для сайтов с JavaScript (опционально)
- Telegram-бот: aiogram 3.x (Long Polling), работает через HTTP API
- httpx: асинхронный HTTP-клиент (webhook, бот, уведомления)
- python-dotenv: загрузка переменных из `.env` файла

**Принцип простоты:** если есть 2 способа — выбирать самый простой и понятный новичку. Всегда добавлять комментарии и docstring.

---

## Структура проекта

```
grabvidzilla/
├── README.md
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .gitignore
├── core/                     # Бизнес-логика (ядро)
│   ├── __init__.py
│   ├── downloader.py         # download_video(), analyze_video() — чистые функции
│   ├── db.py                 # SQLite (data/app.db), Base, SessionLocal, init_db(), модель Download, WAL-режим
│   ├── errors.py             # classify_download_error() — классификация ошибок скачивания
│   ├── auth.py               # Модель User, регистрация/логин, роли (root/admin/user)
│   ├── parser.py             # Статический парсер HTML (requests + BS4)
│   ├── browser_parser.py     # Браузерный парсер (Playwright Chromium)
│   └── site_parsers/         # Site-specific адаптеры
│       ├── __init__.py       # get_adapter_for_url(), register_adapter()
│       ├── base.py           # Базовый класс SiteParserAdapter
│       └── fanserials.py     # Адаптер для fanserials (PlayerJS)
├── cli/
│   ├── __init__.py
│   ├── cli.py                # Локальный CLI (работает напрямую с core)
│   └── cli_api_client.py     # CLI через HTTP API (не импортирует core)
├── api/
│   ├── __init__.py
│   ├── api_main.py           # FastAPI-приложение
│   └── api_service.py        # TaskManager: CRUD задач в БД, TTL-очистка (без скачивания)
├── worker/
│   ├── __init__.py
│   ├── __main__.py           # Позволяет запуск python -m worker
│   └── worker_main.py        # Worker-процесс: polling БД, скачивание, прогресс, graceful shutdown, уведомления (webhook + Telegram)
├── bot/
│   ├── __init__.py
│   ├── __main__.py           # Позволяет запуск python -m bot
│   └── bot_main.py           # Telegram-бот (aiogram 3.x, Long Polling, работает через HTTP API)
├── ui/
│   ├── __init__.py
│   ├── ui_app.py             # Streamlit UI (через HTTP API)
│   └── ui_auth.py            # Формы входа/регистрации, админ-панель
├── tests/
├── tools/                    # ffmpeg, cookies.txt
├── Downloads/                # Папка загрузок по умолчанию
├── data/
│   └── app.db                # SQLite-база пользователей
└── scripts/
    ├── make_executable.sh
    └── create_first_user.py  # Создание первого пользователя (root)
```

---

## Архитектура и границы слоёв

### Жёсткие правила

1. **`core/` — чистое ядро.** Не импортирует `cli/`, `ui/`, `worker/`. Никаких обращений к GUI/терминалу. Только чистые функции + колбеки прогресса.
2. **`api/` импортирует только `core`.** Не импортирует `cli/`/`ui/`/`worker/`. Маппит исключения `core` в HTTP-коды. CORS открыт для dev. **API не скачивает** — только CRUD задач в БД.
3. **`worker/` импортирует только `core`.** Не импортирует `api/`/`cli/`/`ui/`/`bot/`. Забирает задачи из БД и выполняет скачивание. Отправляет уведомления (webhook + Telegram).
4. **`bot/` работает через HTTP API**, не импортирует `core` напрямую. Использует aiogram 3.x с Long Polling.
5. **`cli/cli.py` импортирует `core` напрямую** — локальная работа. Весь вывод через `rich`.
6. **`cli/cli_api_client.py` работает через HTTP API**, не импортирует `core`.
7. **`ui/ui_app.py` работает через HTTP API**, не импортирует `core` напрямую (кроме `ui_auth` для аутентификации).
8. **Ошибки в `core/` не гасим — пробрасываем.** Обработка и форматирование — на уровне `cli`/`ui`.
9. **Никаких циклических импортов.**
10. **Разделение API и Worker:** API (FastAPI) только управляет задачами (CRUD по БД), Worker — только скачивает. Общение — исключительно через общую SQLite БД + общий Docker volume для файлов загрузок.
11. **TaskManager (api/api_service.py)** — CRUD задач в БД: create_task, get_task, list_tasks, cancel_task, TTL-очистка. Не запускает скачивания.
12. **Worker (worker/worker_main.py)** — polling БД каждые N секунд, атомарный захват `queued` задач (`_try_claim_task`), ограничение параллелизма (`MAX_CONCURRENT_DOWNLOADS`), прогресс пишет в БД, graceful shutdown. После завершения задачи отправляет webhook и/или Telegram-уведомление.
13. **Bot (bot/bot_main.py)** — Telegram-бот на aiogram 3.x. Работает через HTTP API (не импортирует `core`). Файлы ≤ 500 MB отправляет через `send_document`, для больших — текстовая ссылка. Не использует `InlineKeyboardButton(url=...)` — Telegram отклоняет локальные URL.

---

## API ядра (core)

```python
# core/downloader.py
download_video(url, output_path=".", progress_callback=None, progress_info_callback=None,
               cookies_path=None, format=None, audio_only=False, subtitle_lang=None,
               desired_basename=None) -> tuple[str, str | None]
# Возвращает (путь к файлу, SHA-256 хеш). Колбек принимает процент [0..100] float.

analyze_video(url, cookies_path=None) -> tuple[dict, list[str], list[str]]
# Анализ ролика без скачивания: (info, qualities, subtitle_langs)

# core/parser.py
find_media_urls(url, cookies_path=None, translation_hash=None)
    -> tuple[list[str], list[str], str | None, list[dict] | None, list[dict]]
# Поиск HLS/файлов, переводы, качества

# core/browser_parser.py
fetch_media_urls_with_browser(url, cookies_path=None, proxy=None, translation_hash=None)
    -> tuple[list[str], list[str], str | None, list[dict] | None, list[dict]]
# Playwright Chromium fallback

# core/errors.py
classify_download_error(e: Exception) -> tuple[str, str]
# Классификация ошибки скачивания: (error_type, error_message). 17 категорий.

# core/site_parsers/
SiteParserAdapter: can_handle(url) -> bool, parse(url, ...) -> tuple[...]
get_adapter_for_url(url) -> SiteParserAdapter | None
register_adapter(adapter)
```

### Аутентификация (core/auth.py)

- Модель `User` (email, name, password, phone, is_active, is_admin, role, timestamps)
- Роли: `root` (суперпользователь, создаётся первым), `admin`, `user`
- `root` не может быть понижен/деактивирован другими
- API: `register_user()`, `authenticate_user()`, `update_user()`, `deactivate_user()`, `delete_user()`, `user_is_admin()`, `user_is_root()`

---

## REST API

- `GET /health` → `{"status":"ok"}`
- `GET /formats?url=...` → info от yt-dlp
- `GET /analyze?url=...` → `{info, qualities, subtitle_langs}`
- `POST /downloads` → `{id}` (тело: `{url, format?, audio_only?, cookies_path?, subtitle_lang?, webhook_url?, telegram_chat_id?}`)
- `GET /downloads` → список задач
- `GET /downloads/{id}` → состояние задачи
- `DELETE /downloads/{id}` → отмена
- `GET /downloads/{id}/file` → файл
- `GET /media` → комбинированный поиск медиа-ссылок (параметры: `url`, `cookies_path?`, `use_browser?`, `fallback_to_browser?`, `translation_hash?`, `proxy_*?`)

ENV (API): `DOWNLOADS_DIR`, `MAX_CONCURRENT_DOWNLOADS`, `QUEUE_STRATEGY`, `PERSIST_DOWNLOADS`, `CLEANUP_INTERVAL_MIN`, `DOWNLOAD_TTL_HOURS`, `GVZ_ALLOW_INSECURE_SSL`.

ENV (Worker): `DOWNLOADS_DIR`, `MAX_CONCURRENT_DOWNLOADS`, `WORKER_POLL_INTERVAL_SEC`, `WORKER_SHUTDOWN_TIMEOUT_SEC`, `PROGRESS_UPDATE_INTERVAL_MS`, `DOWNLOAD_TTL_HOURS`, `TELEGRAM_BOT_TOKEN`, `GVZ_API_URL`, `GVZ_API_PUBLIC_URL`.

ENV (Bot): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`, `GVZ_API_URL`, `GVZ_API_PUBLIC_URL`.

ENV (CLI/UI клиенты): `GVZ_API_URL` (по умолчанию `http://localhost:8000`; в Docker Compose для CLI задаётся как `http://grabvidzilla-api:8000`), `GVZ_ALLOW_INSECURE_SSL`.

---

## Python Style Guide

- **PEP8:** отступы 4 пробела, длина строки ≤ 100 символов.
- **Имена:** функции/переменные — `snake_case`, классы — `PascalCase`.
- **Docstring** в каждом публичном модуле и функции — краткое описание + параметры.
- **Комментарии** простым языком: зачем делаем, а не что делает очевидный код.
- **Логирование:** `logging` только для отладки; печать пользователю — через слои `cli`/`ui`.
- **Исключения:** не создавать кастомные без необходимости. В `core` — понятные `ValueError`/`RuntimeError` с дружелюбным текстом.
- **Аннотации типов** обязательны в `core`, желательны везде.
- **Зависимости** — только из `requirements.txt`.

### Правила для auth/db

- `core/auth.py`: не логировать пароли/секреты; сообщения об ошибках — короткие и понятные; валидация (email, уникальность, роли) в `core`, форматирование — в `cli`/`ui`.
- `core/db.py`: один `engine`, один `SessionLocal`, один `Base`; WAL-режим SQLite для безопасной работы из нескольких процессов (API + Worker); простая автомиграция для новых колонок через `ALTER TABLE`.
  - Модель `Download` содержит поля: `webhook_url` (URL для webhook-уведомления), `webhook_sent` (флаг отправки), `telegram_chat_id` (ID чата Telegram для уведомлений).

---

## Docker / Docker Compose

- Базовый образ: `python:3.11-slim` + `ffmpeg`
- Сервисы: `grabvidzilla-cli`, `grabvidzilla-ui` (порт 8501), `grabvidzilla-api` (порт 8000), `grabvidzilla-worker`, `grabvidzilla-tel-bot`
- Сеть: `grabvidzilla-net` (bridge, все сервисы видят друг друга по имени)
- Тома: `./Downloads`, `./tools`, `./data` (общая SQLite БД между API и Worker)
- **API и Worker — отдельные контейнеры:** API только CRUD, Worker только скачивает. Общение через общую БД (`./data:/app/data`) и общий том загрузок (`./Downloads:/app/Downloads`).
- **CLI в Docker Compose** имеет переменную `GVZ_API_URL=http://grabvidzilla-api:8000`. Для работы CLI через API в контейнере используйте: `docker exec -it grabvidzilla-cli python -m cli.cli_api_client`.
- **Dockerfile не копирует `scripts/` и `data/`** — они исключены в `.dockerignore`. `scripts/` — инструменты разработки, `data/` — монтируется как том при запуске.

## Безопасность

- `GVZ_ALLOW_INSECURE_SSL=1` — принудительное отключение SSL (только при необходимости).
- Пароли хранятся в открытом виде **только для локального/учебного сценария**.
