# GrabVidZilla — Project Rules

## Overview

**Цель:** кроссплатформенный инструмент (Windows/Linux) для скачивания видео (YouTube, VK, Instagram, TikTok и др.) с двумя интерфейсами: CLI и веб-UI (React).

**Технологии:**
- Python 3.11+
- yt-dlp (скачивание), обязательен ffmpeg в системе
- CLI: click + rich (цвета, эмодзи, прогресс)
- UI: React + TypeScript + Vite + Tailwind CSS + shadcn/ui (папка `frontend/app/`, порт 3000)
- REST API: FastAPI + Uvicorn (эндпоинты: /health, /formats, /downloads, /media, /analyze, /stats)
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
├── Dockerfile                # Python образ (api, worker, bot, cli)
├── docker-compose.yml
├── .gitignore
├── core/                     # Бизнес-логика (ядро)
│   ├── __init__.py
│   ├── downloader.py         # download_video(), analyze_video(), convert_to_mp4() — чистые функции
│   ├── db.py                 # SQLite (data/app.db), Base, SessionLocal, init_db(), модель Download, WAL-режим
│   ├── errors.py             # classify_download_error() — классификация ошибок скачивания
│   ├── logger.py             # log_event() — центральная запись в system_logs; не бросает исключений
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
│   ├── api_main.py           # FastAPI-приложение (CORS открыт для frontend/app/)
│   └── api_service.py        # TaskManager: CRUD задач в БД, TTL-очистка (без скачивания)
├── worker/
│   ├── __init__.py
│   ├── __main__.py           # Позволяет запуск python -m worker
│   └── worker_main.py        # Worker-процесс: polling БД, скачивание, прогресс, graceful shutdown, уведомления
├── bot/
│   ├── __init__.py
│   ├── __main__.py           # Позволяет запуск python -m bot
│   └── bot_main.py           # Telegram-бот (aiogram 3.x, Long Polling, работает через HTTP API)
├── frontend/                 # Веб-UI
│   └── app/                  # React проект (Vite + TypeScript + Tailwind CSS + shadcn/ui)
│       ├── src/
│       │   ├── assets/
│       │   │   └── grabvidzilla-logo.png
│       │   ├── api/
│       │   │   └── client.ts         # axios: baseURL='/api', все запросы к FastAPI
│       │   ├── app/
│       │   │   ├── App.tsx           # React Router: /login, /register, /, /dashboard, /history
│       │   │   ├── components/
│       │   │   │   ├── Sidebar.tsx           # боковое меню (логотип, навигация, кнопка выхода)
│       │   │   │   ├── DashboardPage.tsx     # статистика (GET /stats)
│       │   │   │   ├── DownloadsPage.tsx     # скачивание (POST /downloads, polling)
│       │   │   │   ├── ActiveDownloadsPage.tsx
│       │   │   │   ├── DownloadCard.tsx      # карточка задачи
│       │   │   │   ├── HistoryPage.tsx       # история (GET /downloads)
│       │   │   │   ├── LogConsole.tsx
│       │   │   │   ├── PreferencesPage.tsx
│       │   │   │   └── ui/                   # shadcn/ui компоненты
│       │   │   └── pages/
│       │   │       ├── Login.tsx             # страница входа
│       │   │       └── Registration.tsx      # страница регистрации
│       │   ├── styles/                       # CSS/Tailwind стили из Figma
│       │   ├── utils.ts                      # formatBytes(), buildFormatSelector()
│       │   └── main.tsx
│       ├── Dockerfile                        # node:20-alpine, multi-stage build → serve dist/
│       ├── vite.config.ts                    # прокси /api/* → FastAPI :8000 (для dev)
│       ├── package.json
│       ├── tailwind.config.js
│       └── tsconfig.json
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

1. **`core/` — чистое ядро.** Не импортирует `cli/`, `frontend/`, `worker/`. Никаких обращений к GUI/терминалу. Только чистые функции + колбеки прогресса.
2. **`api/` импортирует только `core`.** Не импортирует `cli/`/`frontend/`/`worker/`. Маппит исключения `core` в HTTP-коды. CORS открыт для `frontend/app/` (localhost:5173 в dev, через Caddy в prod). **API не скачивает** — только CRUD задач в БД.
3. **`worker/` импортирует только `core`.** Не импортирует `api/`/`frontend/`/`bot/`. Забирает задачи из БД и выполняет скачивание. Отправляет уведомления (webhook + Telegram).
4. **`bot/` работает через HTTP API**, не импортирует `core` напрямую. Использует aiogram 3.x с Long Polling.
5. **`cli/cli.py` импортирует `core` напрямую** — локальная работа. Весь вывод через `rich`.
6. **`cli/cli_api_client.py` работает через HTTP API**, не импортирует `core`.
7. **`frontend/app/` — чистый React SPA.** Работает только через HTTP API (`/api/*`). Не содержит Python кода. Не импортирует `core` ни в каком виде. Авторизация хранится в `localStorage` ключ `'user'`.
8. **Ошибки в `core/` не гасим — пробрасываем.** Обработка и форматирование — на уровне `cli`/`api`.
9. **Никаких циклических импортов.**
10. **Разделение API и Worker:** API (FastAPI) только управляет задачами (CRUD по БД), Worker — только скачивает. Общение — исключительно через общую SQLite БД + общий Docker volume для файлов загрузок.
11. **TaskManager (api/api_service.py)** — CRUD задач в БД: create_task, get_task, list_tasks, cancel_task, create_convert_task, TTL-очистка. Не запускает скачивания.
12. **Worker (worker/worker_main.py)** — polling БД каждые N секунд, атомарный захват `queued` задач (`_try_claim_task`), ограничение параллелизма (`MAX_CONCURRENT_DOWNLOADS`), прогресс пишет в БД, graceful shutdown. После завершения задачи отправляет webhook и/или Telegram-уведомление. Если `task.convert_to_mp4 == True` — вызывает `convert_to_mp4()` вместо `download_video()`.
13. **Bot (bot/bot_main.py)** — Telegram-бот на aiogram 3.x. Работает через HTTP API (не импортирует `core`). Файлы ≤ 500 MB отправляет через `send_document`, для больших — текстовая ссылка. Не использует `InlineKeyboardButton(url=...)` — Telegram отклоняет локальные URL. После завершения задачи, если файл не `.mp4`, показывает inline-кнопку «Конвертировать в MP4».
    При получении сообщения бот ищет пользователя по `telegram_chat_id` через `GET /users?telegram_chat_id=...`. Если найден — создаёт задачу с `user_id` этого пользователя. Если не найден — fallback на `TELEGRAM_ALLOWED_USERS` из ENV (`user_id=None`). Вспомогательная функция: `_get_user_id_by_telegram(telegram_chat_id: str) -> Optional[int]`.
14. **Логирование через core/logger.py:** API и Worker вызывают `log_event()` напрямую (импортируют `core`).
    Bot пишет логи через `POST /logs` HTTP API (не импортирует `core`).
    `cli_api_client.py` пишет через `POST /logs`. Локальный `cli.py` — только стандартный `logging` в файл, в БД не пишет.
    `log_event()` никогда не бросает исключений — ошибка записи лога не должна прерывать основной процесс.
15. **Фильтрация данных по ролям — только на бэкенде.** Фронтенд отображает то, что вернул бэкенд. Бэкенд всегда проверяет роль вызывающего и фильтрует:
    `root` / `admin` → видят все данные; `user` → только `WHERE user_id = caller_user_id`.
    Это правило применяется к `GET /downloads`, `DELETE /downloads/{id}`, `GET /stats`.
    Фронтенд дублирует логику только для UX (скрывает кнопки), но не как защиту.

---

## Frontend (React)

### Стек
- **React 18** + **TypeScript** — компоненты и логика
- **Vite** — сборщик, dev сервер с hot reload (порт 5173)
- **Tailwind CSS** — стили (акцентный цвет: `#1faa89`)
- **shadcn/ui** — готовые компоненты (button, input, card, progress, table и др.)
- **React Router v6** — навигация между страницами
- **Axios** — HTTP запросы к FastAPI через `/api/*`
- **Recharts** — графики на DashboardPage

### Страницы и роутинг (App.tsx)
- `/login` — Login.tsx: форма входа
- `/register` — Registration.tsx: форма регистрации
- `/` — DownloadsPage.tsx: скачивание (требует авторизации)
- `/dashboard` — DashboardPage.tsx: статистика (требует авторизации)
- `/history` — HistoryPage.tsx: история задач (требует авторизации)
- `/preferences` — PreferencesPage.tsx: настройки (требует авторизации)

Защита роутов: если нет `localStorage.getItem('user')` → редирект на `/login`.

### API клиент (frontend/app/src/api/client.ts)
```typescript
import axios from 'axios'
const api = axios.create({ baseURL: '/api' })
export default api
```
В dev: Vite проксирует `/api/*` → `http://localhost:8000/*`.
В prod: Caddy проксирует `/api/*` → `grabvidzilla-api:8000`.

### Авторизация
Хранится в `localStorage` ключ `'user'`:
```typescript
{ user_id: number, name: string, role: string }
```

### Сборка и запуск
```bash
# Разработка
cd frontend/app
npm run dev        # http://localhost:5173

# Продакшен
npm run build      # собирает в frontend/app/dist/
```

### Дизайн
Компоненты экспортированы из Figma Make. **Не изменять визуальные компоненты** — только добавлять логику.

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

convert_to_mp4(input_path: str, progress_callback=None) -> str
# Конвертирует файл в MP4 (H.264 + AAC + faststart) через ffmpeg.
# Исходный файл удаляется после успешной конвертации. Возвращает путь к новому .mp4.
# Прогресс: парсит out_time_ms из ffmpeg -progress pipe:1.

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

- Модель `User` (email, name, password, phone, is_active, is_admin, role, timestamps, telegram_chat_id — опционально, уникальный, индексированный)
- Роли: `root` (суперпользователь, создаётся первым), `admin`, `user`
- `root` не может быть понижен/деактивирован другими
- API: `register_user()`, `authenticate_user()`, `update_user()`, `deactivate_user()`, `delete_user()`, `user_is_admin()`, `user_is_root()`, `get_user_by_telegram_id(session, telegram_chat_id) -> Optional[User]`

---

## REST API

- `GET /health` → `{"status":"ok"}`
- `GET /formats?url=...` → info от yt-dlp
- `GET /analyze?url=...` → `{info, qualities, subtitle_langs}`
- `POST /downloads` → `{id}` (тело: `{url, format?, audio_only?, cookies_path?, subtitle_lang?, webhook_url?, telegram_chat_id?, source?, user_id?}`)
- `GET /downloads` → список задач
- `GET /downloads/{id}` → состояние задачи
- `DELETE /downloads/{id}` → отмена
- `GET /downloads/{id}/file` → файл
- `POST /downloads/{id}/convert` → `{id}` новой задачи конвертации (query: `telegram_chat_id?`); 404 если задача/файл не найдены, 409 если уже MP4 или задача не completed
- `GET /media` → комбинированный поиск медиа-ссылок (параметры: `url`, `cookies_path?`, `use_browser?`, `fallback_to_browser?`, `translation_hash?`, `proxy_*?`)
- `GET /stats` → агрегированная статистика: `{total, completed, errors, cancelled, active, total_bytes, avg_file_size, top_domains, by_source, by_user, daily}`; `?scope=my` — только задачи текущего пользователя (передаётся `caller_user_id`)
- `GET /users` → список всех пользователей (только root); `?telegram_chat_id=` — поиск по TG ID (для бота, без проверки роли)
- `POST /users` → создание пользователя `{name, email, password, role, telegram_chat_id?}` (только root)
- `GET /users/me` → данные своего профиля (любая роль); `caller_user_id` обязателен; используется фронтендом для загрузки email и telegram_chat_id
- `PUT /users/me` → обновление своих данных `{name?, email?, password?, telegram_chat_id?}` (любая роль); `caller_user_id` обязателен; email можно изменить, если новый адрес не занят
- `PUT /users/{id}` → обновление пользователя (только root); нельзя изменить root-пользователя
- `DELETE /users/{id}` → удаление (только root); нельзя удалить root
- `POST /logs` → запись события `{level, source, event_type, message, user_id?, task_id?, details?, ip_address?}`
- `GET /logs` → список логов с фильтрами `{source?, level?, event_type?, user_id?, date_from?, date_to?, limit?, offset?}`; фильтрация по роли автоматическая (передаётся `caller_role` + `caller_user_id`)
- `DELETE /logs/cleanup` → ручная очистка старых логов по `LOG_RETENTION_DAYS` / `MAX_LOG_ROWS` (только root)

ENV (API): `DOWNLOADS_DIR`, `MAX_CONCURRENT_DOWNLOADS`, `QUEUE_STRATEGY`, `PERSIST_DOWNLOADS`, `CLEANUP_INTERVAL_MIN`, `DOWNLOAD_TTL_HOURS`, `GVZ_ALLOW_INSECURE_SSL`, `LOG_RETENTION_DAYS` (default=0 — бессрочно), `MAX_LOG_ROWS` (default=100000).

ENV (Worker): `DOWNLOADS_DIR`, `MAX_CONCURRENT_DOWNLOADS`, `WORKER_POLL_INTERVAL_SEC`, `WORKER_SHUTDOWN_TIMEOUT_SEC`, `PROGRESS_UPDATE_INTERVAL_MS`, `DOWNLOAD_TTL_HOURS`, `TELEGRAM_BOT_TOKEN`, `GVZ_API_URL`, `GVZ_API_PUBLIC_URL`, `LOG_RETENTION_DAYS` (default=0), `MAX_LOG_ROWS` (default=100000).

ENV (Bot): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`, `GVZ_API_URL`, `GVZ_API_PUBLIC_URL`.

ENV (CLI): `GVZ_API_URL` (по умолчанию `http://localhost:8000`; в Docker Compose задаётся как `http://grabvidzilla-api:8000`), `GVZ_ALLOW_INSECURE_SSL`.

---

## Python Style Guide

- **PEP8:** отступы 4 пробела, длина строки ≤ 100 символов.
- **Имена:** функции/переменные — `snake_case`, классы — `PascalCase`.
- **Docstring** в каждом публичном модуле и функции — краткое описание + параметры.
- **Комментарии** простым языком: зачем делаем, а не что делает очевидный код.
- **Логирование:** Системные события пишутся через `core/logger.py` → `log_event()` в таблицу `system_logs`.
  Стандартный `logging` — только для отладочных сообщений самих модулей (не для пользователя).
  Печать пользователю — через слои `cli`/`api`. `log_event()` всегда вызывается внутри `try/except`.
- **Исключения:** не создавать кастомные без необходимости. В `core` — понятные `ValueError`/`RuntimeError` с дружелюбным текстом.
- **Аннотации типов** обязательны в `core`, желательны везде.
- **Зависимости Python** — только из `requirements.txt`. Зависимости JS — только из `frontend/app/package.json`.

### Правила для auth/db

- `core/auth.py`: не логировать пароли/секреты; сообщения об ошибках — короткие и понятные; валидация (email, уникальность, роли) в `core`, форматирование — в `cli`/`api`.
- `core/logger.py`: не логировать пароли, токены и секреты в поле `details`; при ошибке записи использовать `logging.getLogger`, не бросать исключение.
- `core/db.py`: один `engine`, один `SessionLocal`, один `Base`; WAL-режим SQLite для безопасной работы из нескольких процессов (API + Worker); простая автомиграция для новых колонок через `ALTER TABLE`.
  - Модель `Download` содержит поля: `webhook_url`, `webhook_sent`, `telegram_chat_id`, `convert_to_mp4`, `source` (`"cli"` | `"ui"` | `"bot"` | `"api"` | `None`), `user_id` (INTEGER, FK на users.id, nullable).
  - Автомиграция через `_migrate_add_missing_columns()` добавляет новые колонки через `ALTER TABLE`.
  - Колонка `telegram_chat_id` в таблице `users` — уникальная, nullable. Два пользователя не могут иметь одинаковый Telegram ID. Проверка уникальности реализована в `update_user()` через `ValueError`.
  - Автомиграция для `users.telegram_chat_id` добавлена в `_migrate_add_missing_columns()` по аналогии с миграциями таблицы `downloads` (проверка через `PRAGMA table_info(users)`, добавление через `ALTER TABLE`).

---

## Docker / Docker Compose

- Базовый образ Python: `python:3.11-slim` + `ffmpeg`
- Базовый образ Frontend: `node:20-alpine` (multi-stage: сборка → serve)
- Сервисы:
  - `grabvidzilla-cli` — CLI
  - `grabvidzilla-frontend` — React UI (порт 3000)
  - `grabvidzilla-api` — FastAPI (внешний порт 8585 → внутренний 8000)
  - `grabvidzilla-worker` — Worker скачивания
  - `grabvidzilla-tel-bot` — Telegram бот
- Сеть: `grabvidzilla-net` (bridge, все сервисы видят друг друга по имени)
- Тома: `./Downloads`, `./tools`, `./data` (общая SQLite БД между API и Worker)
- **API и Worker — отдельные контейнеры:** общаются только через общую БД и том загрузок.
- **Dockerfile (Python)** не копирует `ui/`, `scripts/`, `data/` и `frontend/`.
- **frontend/app/Dockerfile** — отдельный образ для React.
- На сервере Linux: Caddy проксирует порт 80 → frontend:3000, `/api/*` → api:8000.
- На Windows в разработке: Vite dev сервер (`:5173`) проксирует `/api/*` → FastAPI (`:8000`).

## Безопасность

- `GVZ_ALLOW_INSECURE_SSL=1` — принудительное отключение SSL (только при необходимости).
- Пароли хранятся в открытом виде **только для локального/учебного сценария**.
- CORS в FastAPI разрешён для `http://localhost:5173` (Vite dev) и продакшен домена.