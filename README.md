# GrabVidZilla

Кроссплатформенный инструмент для скачивания видео с различных платформ (YouTube, VK, Instagram, TikTok и др.).

## Возможности

- 🎥 Загрузка видео с множества платформ (через yt-dlp)
- 💻 CLI интерфейс (click + rich): прогресс-бар, скорость скачивания, цветные сообщения и эмодзи
  - Локальный CLI (`cli/cli.py`) — работает напрямую с ядром
  - API-клиент (`cli/cli_api_client.py`) — работает через HTTP API
- 🌐 Веб-интерфейс (React + TypeScript): анализ форматов, выбор качества и субтитров, прогресс скачивания (работает через HTTP API)
  - Страница «⬇️ Загрузки» — основной интерфейс скачивания
  - Страница «📊 Dashboard» — статистика: KPI-карточки, графики, топ сайтов, источники загрузок, системный статус
  - 📜 Система логирования: история событий (скачивания, ошибки, авторизация) с разграничением по ролям. Root видит все логи системы, admin — задачи всех пользователей, user — только свои.
  - Страница «📋 История» — история всех задач
  - Страница «⚙️ Настройки» — настройки приложения
- 👤 Управление пользователями: root может создавать, редактировать и удалять пользователей
  через вкладку «Пользователи» в Настройках. Каждый пользователь может привязать свой
  Telegram ID в настройках аккаунта для использования Telegram-бота.
- 🔌 REST API (FastAPI) + Worker: API управляет задачами (CRUD), отдельный Worker-процесс выполняет скачивание. Общение через общую SQLite БД
- 🔎 Поиск видео на веб‑страницах (HLS m3u8 и прямые ссылки) с выбором перевода и качества, Playwright fallback для динамических сайтов
- 🧾 Проверка целостности: SHA-256 после каждой загрузки
- 🧩 Поддержка cookies.txt (Netscape) для приватных/региональных видео
- 🤖 Telegram-бот (aiogram 3.x): скачивание прямо из чата
- 🐳 Docker-образ (включает ffmpeg)

## Стек технологий

| Слой | Технология |
|------|-----------|
| Скачивание | yt-dlp + ffmpeg |
| Бэкенд API | FastAPI + Uvicorn |
| База данных | SQLite + SQLAlchemy |
| Веб-UI | React + TypeScript + Vite + Tailwind CSS + shadcn/ui |
| CLI | click + rich |
| Telegram бот | aiogram 3.x |
| Парсинг | requests + BeautifulSoup4 + Playwright |
| Инфраструктура | Docker + Docker Compose + Caddy |

## Требования

- Python 3.11+
- Node.js 20+ (для веб-UI в режиме разработки)
- ffmpeg (в системе) — для локального запуска; в Docker ставится автоматически
- Рекомендуется актуальный `yt-dlp` (зафиксирован в `requirements.txt`)

## Установка (локально)

### 1. Создать и активировать venv

```bash
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Linux/Mac:
# source .venv/bin/activate
```

### 2. Установить Python зависимости

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### 3. Установить зависимости фронтенда

```bash
cd frontend/app
npm install
```

### 4. Создать первого пользователя (root)

```bash
python scripts/create_first_user.py
```

### 5. Добавить ffmpeg в PATH (если не установлен системно)

- Windows: добавьте путь к бинарнику в PATH
- Или используйте Docker — там ffmpeg ставится автоматически

## Запуск для разработки (без Docker)

```powershell
# Терминал 1 — FastAPI
uvicorn api.api_main:app --reload --host 0.0.0.0 --port 8000

# Терминал 2 — Worker
python -m worker.worker_main

# Терминал 3 — React UI
cd frontend\app
npm run dev
```

Открыть в браузере: `http://localhost:5173`

## Запуск через Docker (рекомендуется)

```bash
# Скопировать и настроить переменные окружения
cp .env.example .env

# Собрать и запустить все сервисы
docker compose up --build -d

# Windows: http://localhost:3000
# Linux с Caddy: http://твой-ip
```

## Docker сервисы

| Сервис | Описание | Порт |
|--------|----------|------|
| `grabvidzilla-frontend` | React веб-UI | 3000 |
| `grabvidzilla-api` | FastAPI REST API | 8585→8000 |
| `grabvidzilla-worker` | Фоновое скачивание | — |
| `grabvidzilla-tel-bot` | Telegram бот | — |
| `grabvidzilla-cli` | CLI в контейнере | — |

### Полезные команды Docker

```bash
# Запустить всё
docker compose up -d

# Остановить
docker compose down

# Логи конкретного сервиса
docker compose logs -f grabvidzilla-api
docker compose logs -f grabvidzilla-frontend

# Пересобрать после изменений
docker compose up --build -d

# CLI в контейнере
docker exec -it grabvidzilla-cli python -m cli.cli_api_client
```

## Структура проекта

```
grabvidzilla/
├── core/          # Бизнес-логика (скачивание, БД, авторизация, парсинг)
│   └── logger.py  # Центральный модуль логирования: log_event()
├── api/           # FastAPI REST API
├── worker/        # Фоновый процесс скачивания
├── bot/           # Telegram-бот (aiogram)
├── cli/           # CLI интерфейс (click + rich)
├── frontend/      # Веб-UI
│   └── app/       # React проект (Vite + TypeScript + Tailwind + shadcn/ui)
├── scripts/       # Вспомогательные скрипты
├── tools/         # ffmpeg, cookies.txt
├── Downloads/     # Папка загрузок
└── data/          # SQLite база данных
```

## Использование: Веб-UI (React)

Открыть `http://localhost:5173` (разработка) или `http://localhost:3000` (Docker).

Основной поток работы:
1. Войти в систему на странице `/login`
2. Ввести URL видео на странице «Загрузки»
3. Нажать «Анализ» — получить метаданные и список качеств
4. Выбрать качество и нажать «Скачать»
5. Следить за прогрессом в реальном времени
6. Файл сохраняется в папку `Downloads/`

## Использование: CLI

### Локальный CLI (работает напрямую с ядром)

```bash
python -m cli.cli
```

### API-клиент (работает через HTTP API)

```bash
python -m cli.cli_api_client
```

### Примеры

```bash
python -m cli.cli "https://youtu.be/..." -o ".\Downloads"
python -m cli.cli "https://vkvideo.ru/..." --cookies tools\cookies.txt
```

### Скачивание нескольких видео

```bash
# Из файла со списком URL (по одному на строку)
python -m cli.cli --batch urls.txt -o Downloads/
```

## REST API

Документация доступна после запуска: `http://localhost:8000/docs`

| Метод | Эндпоинт | Описание |
|-------|----------|----------|
| GET | `/health` | Статус сервиса |
| GET | `/analyze?url=` | Анализ видео (качества, субтитры) |
| POST | `/downloads` | Создать задачу скачивания |
| GET | `/downloads` | Список задач |
| GET | `/downloads/{id}` | Статус задачи |
| DELETE | `/downloads/{id}` | Отменить задачу |
| GET | `/downloads/{id}/file` | Скачать файл |
| POST | `/downloads/{id}/convert` | Конвертировать в MP4 |
| GET | `/media?url=` | Поиск HLS и прямых ссылок |
| GET | `/stats` | Статистика для Dashboard |
| GET | `/users` | Список всех пользователей (только root) |
| POST | `/users` | Создать пользователя (только root) |
| PUT | `/users/{id}` | Обновить пользователя (только root) |
| DELETE | `/users/{id}` | Удалить пользователя (только root) |
| GET | `/users/me` | Получить свои данные (любая роль) |
| PUT | `/users/me` | Обновить свои данные, включая email (любая роль) |
| POST | `/logs` | Записать событие (используется Bot и CLI) |
| GET | `/logs` | Получить логи (фильтры: source, level, event_type, date, user) |
| DELETE | `/logs/cleanup` | Ручная очистка старых логов (только root) |

## Telegram-бот

### Настройка

1. Получите токен бота у [@BotFather](https://t.me/BotFather)
2. Узнайте свой `user_id` (через [@userinfobot](https://t.me/userinfobot))
3. Заполните `.env` файл:

**Привязка аккаунта:** зарегистрированные пользователи GrabVidZilla могут привязать
Telegram-аккаунт в разделе Настройки → Мой аккаунт → поле «Telegram ID».
Узнать свой Telegram ID можно у [@userinfobot](https://t.me/userinfobot).
После привязки задачи из бота будут отображаться в истории веб-интерфейса под вашим аккаунтом.
Если Telegram ID не привязан — бот работает через список `TELEGRAM_ALLOWED_USERS` в `.env` (старый режим).

```env
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_ALLOWED_USERS=123456789,987654321
GVZ_API_PUBLIC_URL=http://your-server-ip:8585
```

### Запуск

```bash
# Через Docker Compose
docker compose up -d grabvidzilla-api grabvidzilla-worker grabvidzilla-tel-bot

# Локально
python -m bot.bot_main
```

### Возможности

- Отправьте ссылку → бот анализирует и предлагает выбор формата
- Прогресс скачивания в реальном времени
- Файлы ≤ 500 MB отправляются как документ прямо в чат
- Файлы > 500 MB — текстовая ссылка для скачивания
- Inline-кнопка «Конвертировать в MP4» для не-mp4 файлов
- Команды: `/queue`, `/history`, `/cancel <id>`, `/help`

## Переменные окружения

```env
# Telegram
TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_ALLOWED_USERS=123456789

# Публичный URL API (для ссылок в уведомлениях)
GVZ_API_PUBLIC_URL=http://your-server-ip:8585

# SSL (отключить если нет сертификата)
GVZ_ALLOW_INSECURE_SSL=0

# Логирование
LOG_RETENTION_DAYS=0       # 0 = хранить бессрочно; >0 = удалять записи старше N дней
MAX_LOG_ROWS=100000        # максимальное кол-во строк в таблице system_logs (жёсткий потолок)
```

## Роли пользователей

| Роль | Возможности | Данные | Логи |
|------|------------|--------|------|
| `root` | Всё. Создаётся через `scripts/create_first_user.py`. Не может быть удалён. Управление пользователями через вкладку «Пользователи» в Настройках. | Все данные всех пользователей | Все логи системы |
| `admin` | Просмотр всех задач и истории. Без управления пользователями. | Все данные всех пользователей | Только задачи (`download_*`, `convert_*`) |
| `user` | Скачивание видео, просмотр своих задач | Только свои данные во всех разделах | Только свои задачи |

## Динамический парсинг (Playwright)

Некоторые сайты отдают ссылки только после выполнения JavaScript. Для таких случаев используется Playwright:

```bash
pip install -r requirements.txt
python -m playwright install chromium

# Linux (дополнительные зависимости):
python -m playwright install --with-deps chromium
```

## Webhook-уведомления

```bash
curl -X POST "http://localhost:8000/downloads" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://youtube.com/watch?v=...","webhook_url":"http://your-server/callback"}'
```

## Масштабирование Worker

```bash
docker compose up -d --scale grabvidzilla-worker=3
```

## FAQ / Траблшутинг

- **403 / "Failed to parse JSON"**: используйте актуальные cookies, попробуйте другой аккаунт/регион
- **yt-dlp устарел**: `pip install -U yt-dlp` или пересоберите Docker
- **UI не открывается**: проверьте что FastAPI запущен на порту 8000
- **CORS ошибки**: в разработке Vite проксирует запросы — убедитесь что `npm run dev` запущен

## Лицензия

MIT