# GrabVidZilla

Кроссплатформенный инструмент для скачивания видео с различных платформ (YouTube, VK, Instagram, TikTok и др.).

## Возможности

- 🎥 Загрузка видео с множества платформ (через yt-dlp)
- 💻 CLI интерфейс (click + rich): прогресс-бар, скорость скачивания, цветные сообщения и эмодзи
  - Локальный CLI (`cli/cli.py`) — работает напрямую с ядром
  - API-клиент (`cli/cli_api_client.py`) — работает через HTTP API
- 🖥️ Графический интерфейс (Streamlit): анализ форматов, выбор качества и субтитров, прогресс и кнопка скачивания файла в браузере (работает через HTTP API)
- 🌐 REST API (FastAPI): централизованная система скачивания через TaskManager с ограничением параллелизма и очередью задач
- 🔎 Поиск видео на веб‑страницах в CLI (HLS m3u8 и прямые ссылки на файлы) с последующей загрузкой
- 🧩 Поддержка cookies.txt (Netscape) для приватных/региональных видео (VK/YouTube и др.)
- 🐳 Docker-образ (включает ffmpeg)

## Требования

- Python 3.11+
- ffmpeg (в системе) — для локального запуска; в Docker ставится автоматически
- Рекомендуется актуальный `yt-dlp` (в проекте зафиксирован в `requirements.txt`)
- Используемые библиотеки (Python): `yt-dlp`, `streamlit`, `click`, `rich`, `requests`, `beautifulsoup4` (см. `requirements.txt`)

## Установка (локально)

1) Создайте и активируйте venv:
```bash
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Linux/Mac:
# source .venv/bin/activate
```

2) Установите зависимости:
```bash
pip install -r requirements.txt
```

3) Добавьте ffmpeg в PATH (если не установлен системно):
- Windows (пример): добавьте путь к бинарнику в PATH текущей сессии.
- Или используйте Docker (ниже) — там ffmpeg уже ставится.

## Использование: UI (Streamlit)

**Важно:** UI работает через HTTP API. Перед запуском UI необходимо запустить API сервер (см. раздел REST API).

Локальный запуск из корня проекта (после установки зависимостей):

```bash
# 1. Сначала запустите API сервер (в отдельном терминале)
uvicorn api.api_main:app --reload --host 0.0.0.0 --port 8000

# 2. Затем запустите UI (в другом терминале)
# вариант A
streamlit run ui/ui_app.py

# вариант B (эквивалентно)
python -m streamlit run ui/ui_app.py
```

Откроется интерфейс по адресу `http://localhost:8501`.

- Основной поток работы:
  - Введите URL видео.
  - Нажмите «Analysis» — получим метаданные, список доступных качеств и языков субтитров (через `GET /analyze`).
  - Выберите качество и (опционально) язык субтитров.
  - Нажмите «Download» — отправляется запрос на скачивание (`POST /downloads`), затем отслеживается прогресс через `GET /downloads/{id}`.
  - По завершении появится кнопка «Скачать файл в браузере» (через `GET /downloads/{id}/file`) и файл будет сохранён на диск.
- Cookies:
  - Разверните «Advanced (cookies)» и загрузите файл формата Netscape (`cookies.txt`).
  - Если файл уже лежит в `tools/cookies.txt`, UI подхватит его автоматически.
  - Cookies передаются в API через параметр `cookies_path` в запросах.
- Кнопка «Stop server» останавливает процесс Streamlit (аналог `Ctrl+C`).

Особенности выбора формата в UI:
- «audio only» — использует `bestaudio/best`.
- Для `<Xp>` UI формирует безопасный селектор вида `bv*[height<=X]+ba/best[height<=X]`.
- Если запрошенный формат недоступен, выполняется фолбэк на `best`.

Где сохраняются файлы (UI):
- По умолчанию — в папку, указанную в `DOWNLOADS_DIR` переменной окружения API (по умолчанию `downloads`).

## Использование: CLI

### Локальный CLI (работает напрямую с ядром)

Запуск из корня проекта:
```bash
python -m cli.cli
```

- Меню:
  - 1. Скачать видео
  - 2. help
  - 3. Загрузить cookies (скопирует указанный файл в `tools/cookies.txt`)
  - 4. Найти видео на странице (поиск HLS m3u8 и прямых ссылок на видео)
  - 0. Выход

- Прямые примеры:
```bash
python -m cli.cli "https://youtu.be/..." -o ".\Downloads"
python -m cli.cli "https://vkvideo.ru/..." --cookies tools\cookies.txt
```

- Отображаем:
  - Прогресс (0..100%)
  - Текущую скорость (Б/с, КБ/с, МБ/с)
  - По завершении — имя файла, время скачивания и размер файла

- Путь по умолчанию:
  - Меню: `Downloads` в корне проекта
  - CLI: `-o/--output`, если не задано — также `Downloads`

### API-клиент (работает через HTTP API)

**Важно:** Перед запуском API-клиента необходимо запустить API сервер (см. раздел REST API).

Запуск из корня проекта:
```bash
python -m cli.cli_api_client
```

- Меню аналогично локальному CLI, но все операции выполняются через REST API:
  - 1. Скачать видео (через `POST /downloads` и отслеживание прогресса)
  - 2. help
  - 3. Загрузить cookies (объединение с существующими в `tools/cookies.txt`)
  - 4. Найти видео на странице (пока не реализовано через API)
  - 0. Выход

- Переменные окружения:
  - `GVZ_API_URL` — URL API сервера (по умолчанию `http://localhost:8000`)

- Примеры:
```bash
# С API на localhost:8000 (по умолчанию)
python -m cli.cli_api_client

# С API на другом адресе
GVZ_API_URL=http://api.example.com:8000 python -m cli.cli_api_client
```

- Отображаем:
  - Прогресс (0..100%) через периодический опрос API
  - Текущую скорость (Б/с, КБ/с, МБ/с)
  - По завершении — имя файла, время скачивания и размер файла

- Преимущества API-клиента:
  - Единый лимит параллельных загрузок (`MAX_CONCURRENT_DOWNLOADS`)
  - Единая очередь задач
  - Можно использовать несколько клиентов одновременно

#### Поиск видео на странице (пункт 4 меню CLI)

- В пункте «4. Найти видео на странице» вы можете ввести URL обычной веб‑страницы:
  - `core/parser.py` с помощью `requests` + `BeautifulSoup` загружает HTML и ищет в нём ссылки на:
    - HLS-потоки (`*.m3u8`, включая ссылки, спрятанные в player‑URL вроде `...?file=...hls.m3u8`),
    - прямые видеофайлы (`.mp4`, `.webm` и т.д.).
  - CLI показывает нумерованный список найденных ссылок (сначала HLS, затем файлы) и предлагает выбрать номер для скачивания.
  - Далее используется тот же движок `download_video`, что и для обычных URL (поддерживается прогресс, скорость, переименование файла и т.п.).

- Ограничения:
  - Парсер видит только то, что есть в **статическом HTML**; если сайт подгружает плеер и потоки только через JavaScript, некоторые варианты озвучки/качества могут не появиться в списке.
  - Для сложных сайтов можно скопировать прямую ссылку на `*.m3u8` или файл из инструментов разработчика браузера и использовать пункт «1. Скачать видео`» напрямую.

## Cookies (Netscape формат)

Зачем: VK/YouTube часто требуют авторизацию/регион → без cookies возможны 403/ошибки.

Как получить:
1) Войдите на сайт в своём браузере.
2) Экспортируйте cookies в текстовый файл формата Netscape (расширения “Get cookies.txt” или аналог).
3) Варианты использования:
   - Меню → пункт 3 “Загрузить cookies” → укажите путь — файл скопируется в `tools/cookies.txt`; далее пункт 1.
   - Прямо в CLI: `--cookies tools\cookies.txt`.

Важно: храните файл в секрете — внутри ваши сессии.

## Docker

Сборка:
```bash
docker build -t grabvidzilla .
```

Примеры запуска:
```bash
# Windows PowerShell: создадим папку для загрузок
New-Item -ItemType Directory -Path .\downloads -Force | Out-Null

# Меню (интерактивно, нужен -it). Файлы сохраняйте в /data.
docker run --rm -it -v "${PWD}\downloads:/data" grabvidzilla

# Прямой URL → сохранение в /data
docker run --rm -it -v "${PWD}\downloads:/data" grabvidzilla "https://youtu.be/..." -o /data

# С cookies (смонтируем cookies.txt в контейнер)
docker run --rm -it -v "${PWD}\downloads:/data" -v "${PWD}\tools\cookies.txt:/app/tools/cookies.txt:ro" \
  grabvidzilla "https://vkvideo.ru/..." -o /data --cookies /app/tools/cookies.txt

# Показать помощь
docker run --rm grabvidzilla --help
```

### Где лежат файлы и как монтировать папки (Windows/Linux)

- Рекомендуемый способ — сохранять сразу в смонтированную папку хоста, чтобы контейнер не засорялся.
- Есть два удобных варианта:
  - Сохранять в `/data` и монтировать её на хостовую папку.
  - Или монтировать хостовую папку в `/app/Downloads` и пользоваться дефолтным путём меню.

Примеры монтирования (подставьте свою папку):

- Windows PowerShell (пример для `D:\temp\Downloads`):
```powershell
# Вариант A: сохраняем прямо в /data (рекомендуется для прямых команд)
docker run --rm -it -v "D:\temp\Downloads:/data" grabvidzilla "https://youtu.be/..." -o /data

# Вариант B: меню с дефолтным путём (меню сохраняет в /app/Downloads)
docker run --rm -it -v "D:\temp\Downloads:/app/Downloads" grabvidzilla
```

- Linux/macOS (пример для `~/temp/Downloads`):
```bash
# Вариант A: сохраняем прямо в /data
docker run --rm -it -v "$HOME/temp/Downloads:/data" grabvidzilla "https://youtu.be/..." -o /data

# Вариант B: меню с дефолтным путём
docker run --rm -it -v "$HOME/temp/Downloads:/app/Downloads" grabvidzilla
```

Примечания:
- Если указываете `-o /data`, итоговый файл сразу попадёт на хост — внутри контейнера ничего лишнего не останется.
- Если используете меню без `-o`, смонтируйте хостовую папку в `/app/Downloads`, чтобы файлы сохранялись туда и были видны на хосте.

### Запуск UI (Streamlit) в Docker

По умолчанию образ запускает CLI. Для UI переопределите точку входа и пробросьте порт 8501.

- Windows PowerShell (пример, сохраняем на хост в `D:\temp\Downloads`):
```powershell
docker run --rm -p 8501:8501 `
  --entrypoint streamlit `
  -e HOME=/app `
  -v "D:\temp\Downloads:/app/Downloads" `
  -v "D:\ProjectsLab\GrabVidZilla-V2\tools:/app/tools" `
  grabvidzilla run ui/ui_app.py --server.address=0.0.0.0 --server.port=8501
```

- Linux/macOS (пример, сохраняем в `~/temp/Downloads`):
```bash
docker run --rm -p 8501:8501 \
  --entrypoint streamlit \
  -v "$HOME/temp/Downloads:/app/Downloads" \
  -v "$(pwd)/tools:/app/tools" \
  grabvidzilla run ui/ui_app.py --server.address=0.0.0.0 --server.port=8501
```

Пояснения:
- Переменная `HOME=/app` сделает домашней директорией `/app`, чтобы UI сохранял в `/app/Downloads`.
- Смонтируйте `tools` при необходимости использовать `cookies.txt` в UI.

### Cookies внутри Docker

Варианты:
- Смонтировать файл cookies напрямую в контейнер и указать его при запуске:
  - Windows:
  ```powershell
  docker run --rm -it -v "D:\temp\Downloads:/data" -v "D:\ProjectsLab\GrabVidZilla-V2\tools\cookies.txt:/app/tools/cookies.txt:ro" `
    grabvidzilla "https://vkvideo.ru/..." -o /data --cookies /app/tools/cookies.txt
  ```
  - Linux/macOS:
  ```bash
  docker run --rm -it -v "$HOME/temp/Downloads:/data" -v "$(pwd)/tools/cookies.txt:/app/tools/cookies.txt:ro" \
    grabvidzilla "https://vkvideo.ru/..." -o /data --cookies /app/tools/cookies.txt
  ```
- Использовать меню CLI (пункт 3 “Загрузить cookies”):
  - Чтобы cookies сохранялись и между запусками, смонтируйте папку `tools` целиком:
    - Windows:
    ```powershell
    docker run --rm -it -v "D:\temp\Downloads:/app/Downloads" -v "D:\ProjectsLab\GrabVidZilla-V2\tools:/app/tools" grabvidzilla
    ```
    - Linux/macOS:
    ```bash
    docker run --rm -it -v "$HOME/temp/Downloads:/app/Downloads" -v "$(pwd)/tools:/app/tools" grabvidzilla
    ```
  - В меню укажите путь к cookies-файлу внутри контейнера (можно к смонтированной папке), файл будет скопирован в `/app/tools/cookies.txt`.

### Вызов меню и Help в Docker

- Меню CLI (ENTRYPOINT запускает CLI без аргументов):
```bash
docker run --rm -it grabvidzilla
```
- Help (работает в Docker):
```bash
docker run --rm grabvidzilla --help
```

### Запуск через Docker Compose

В репозитории есть файл `docker-compose.yml`, который упрощает запуск CLI и UI, а также заранее создаёт общую Docker-сеть для интеграции с другими контейнерами.

- **Тома и папки:**
  - Локальная папка `Downloads` монтируется в контейнер как `/app/Downloads` и `/data` — сюда сохраняются все загруженные видео.
  - Локальная папка `tools` монтируется в контейнер как `/app/tools` — внутри ожидается файл `cookies.txt` для авторизации на сайтах (формат Netscape).
  - Если папок `Downloads` или `tools` нет, создайте их рядом с `docker-compose.yml`.

- **Сеть:**
  - В `docker-compose.yml` объявлена пользовательская сеть `grabvidzilla-net`.
  - Оба сервиса (`grabvidzilla-cli` и `grabvidzilla-ui`) подключены к этой сети.
  - Любые другие контейнеры, запущенные через docker-compose или вручную с опцией `--network grabvidzilla-net`, смогут обращаться к GrabVidZilla по имени сервиса (например, `grabvidzilla-cli`).

- **Запуск CLI (интерактивное меню):**
  - Собрать и запустить контейнер с меню:
    ```bash
    docker compose up grabvidzilla-cli
    ```
  - Файлы будут сохраняться в локальную папку `Downloads` рядом с проектом.

- **Запуск UI (Streamlit):**
  - Запустить UI:
    ```bash
    docker compose up grabvidzilla-ui
    ```
  - Интерфейс будет доступен по адресу `http://localhost:8501`.
  - Скачанные файлы также будут складываться в локальную папку `Downloads`.

При необходимости вы можете добавить другие сервисы в этот же `docker-compose.yml` и подключить их к сети `grabvidzilla-net` для взаимодействия с GrabVidZilla.

### Где Docker хранит данные по умолчанию
- Windows (Docker Desktop с WSL2): виртуальный диск в `%USERPROFILE%\AppData\Local\Docker\wsl\data\ext4.vhdx`
- Linux: `/var/lib/docker`

### Сеть Docker для связи с другими контейнерами

- В Docker-образе по умолчанию задано имя сети в переменной окружения `GVZ_DOCKER_NETWORK=grabvidzilla-net` (см. `Dockerfile`).
- Эту сеть можно создать один раз на хосте и использовать для связи GrabVidZilla с другими контейнерами (БД, прокси и т.п.).

Создание сети:

```bash
docker network create grabvidzilla-net
```

Пример запуска CLI в этой сети:

```bash
docker run --rm -it \
  --network grabvidzilla-net \
  -v "${PWD}/downloads:/data" \
  --name grabvidzilla \
  grabvidzilla
```

Любые другие контейнеры, запущенные с `--network grabvidzilla-net`, смогут обращаться к GrabVidZilla по имени контейнера `grabvidzilla`.

## Установка на Linux-сервер из GitHub

Ниже пример установки GrabVidZilla на Linux-сервер, если исходники лежат на GitHub.

### Вариант A: установка в виртуальное окружение (без Docker)

1) Установите git и Python 3.11+ (пример для Debian/Ubuntu):

```bash
sudo apt update
sudo apt install -y git python3.11 python3.11-venv ffmpeg
```

2) Клонируйте репозиторий (замените `USER` и URL на свой):

```bash
cd /opt
sudo git clone https://github.com/USER/GrabVidZilla-V2.git grabvidzilla
cd grabvidzilla
```

3) Создайте и активируйте виртуальное окружение:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

4) Установите зависимости:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

5) Запустите CLI или UI:

- CLI меню:

  ```bash
  python -m cli.cli
  ```

- UI (Streamlit):

  ```bash
  python -m streamlit run ui/ui_app.py --server.address=0.0.0.0 --server.port=8501
  ```

  После этого UI будет доступен по адресу `http://<IP_сервера>:8501`.

### Вариант B: установка и запуск через Docker Compose на сервере

1) Установите Docker и docker-compose-plugin (для Ubuntu, пример):

```bash
sudo apt update   # обновляет локальный список пакетов из репозиториев
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker  # включает автозапуск Docker и сразу его запускает
```

2) Клонируйте репозиторий (замените `USER` и URL на свой):

```bash
cd /opt
sudo git clone https://github.com/USER/GrabVidZilla-V2.git grabvidzilla
cd grabvidzilla
```

3) Подготовьте папки для загрузок и cookies (они будут монтироваться в контейнеры):

```bash
sudo mkdir -p Downloads tools
```

4) Соберите образы через docker-compose (используется локальный `Dockerfile`):

```bash
sudo docker compose build
```

5) Запуск CLI (интерактивное меню) на сервере:

```bash
sudo docker compose run --rm grabvidzilla-cli
```

Файлы будут сохраняться в `/opt/grabvidzilla/Downloads` на сервере.

6) Запуск UI (Streamlit) на сервере:

```bash
sudo docker compose up -d grabvidzilla-ui
```

После этого UI будет доступен по адресу `http://<IP_сервера>:8501`. Все загруженные файлы также будут складываться в `/opt/grabvidzilla/Downloads`.

### Обновление GrabVidZilla при изменениях в GitHub

Если вы обновили репозиторий на GitHub (или вышла новая версия), на сервере достаточно:

#### Вариант A (venv, без Docker)

```bash
cd /opt/grabvidzilla
git pull
source .venv/bin/activate
pip install -r requirements.txt
```

После этого можно снова запускать CLI/UI обычным способом.

#### Вариант B (Docker / Docker Compose)

```bash
cd /opt/grabvidzilla
sudo git pull
sudo docker compose build
sudo docker compose up -d grabvidzilla-ui
```

После пересборки и перезапуска сервисов `docker compose` автоматически подхватит обновлённый образ.

## Структура проекта

```
grabvidzilla/
├── core/          # Бизнес-логика загрузки видео и пользователей
├── cli/           # Командная строка (click + rich)
├── ui/            # Графический интерфейс (Streamlit)
├── tools/         # Внешние утилиты, cookies.txt, ffmpeg
├── Downloads/     # Папка загрузок по умолчанию (CLI и меню)
├── tests/         # Тесты
└── scripts/       # Утилиты разработки
```

### Аутентификация и база данных пользователей

- Модуль БД: `core/db.py`
  - SQLite-файл по умолчанию: `data/app.db` (создаётся автоматически при первом запуске).
  - Содержит:
    - `engine` — подключение к SQLite;
    - `SessionLocal` — фабрика сессий;
    - `Base` — базовый класс для ORM-моделей;
    - `init_db()` — создаёт все таблицы.
- Пользователи и пароли: `core/auth.py`
  - ORM-модель `User` со следующими полями:
    - `id` — первичный ключ;
    - `email` — уникальный e-mail;
    - `name` — имя пользователя;
    - `password` — пароль **в открытом виде** (подходит только для локальной/учебной среды);
    - `phone` — телефон (опционально);
    - `is_active: bool` — активен ли пользователь (можно «выключить» без удаления);
    - `is_admin: bool` — флаг администратора;
    - `role: str` — роль пользователя (`"root"`, `"admin"` или `"user"`);
    - `created_at` — дата создания;
    - `updated_at` — дата последнего обновления.
  - Основные функции:
    - `register_user(db, email, name, password, phone, role, is_admin, is_active)` — регистрация (в UI по умолчанию создаются обычные пользователи с ролью `"user"`);
    - `authenticate_user(db, email, password)` — логин по email/имени и паролю (учитывает `is_active`);
    - `update_user(db, user_id, ...)` / `deactivate_user(db, user_id)` — обновление и отключение пользователей;
    - `user_is_admin(user)` — проверка прав администратора (включая `root`);
    - `user_is_root(user)` — проверка прав суперпользователя;
    - `user_to_dict(user)` — удобное представление для UI/API.
- UI-обвязка: `ui/ui_auth.py`
  - Блок аутентификации (логин/регистрация):
    - форма входа (`email` + `password`);
    - форма регистрации (`email`, `name`, `password`, `phone`).
  - В `st.session_state["current_user"]` хранится словарь с полями:
    - `id`, `email`, `name`, `role`, `is_admin`, `is_active`.
  - Вспомогательные функции:
    - `render_auth_block()` — отображает блок входа/регистрации или информацию о текущем пользователе;
    - `require_login()` — требует авторизацию для доступа к разделу (иначе показывает формы и останавливает страницу);
    - `require_admin()` — требует права администратора;
    - `logout()` — выход из аккаунта.
- В `ui/ui_app.py`:
  - В начале `main()` вызывается `ui_auth.render_auth_block()` и `ui_auth.require_login()`, чтобы доступ к функционалу загрузки был только у авторизованных пользователей.

> **Важно:** пароли хранятся без хеширования. Это сделано специально для простоты локальной отладки и возможности менять пароль напрямую через SQLite (`data/app.db`). Для реальных проектов настоятельно рекомендуется использовать безопасное хеширование паролей.

#### Создание первого пользователя

При самом первом запуске UI (`streamlit run ui/ui_app.py`), если в базе ещё нет ни одного пользователя, вы увидите сообщение с инструкцией создать первого пользователя через CLI.

Сделайте это один раз:

```bash
python scripts/create_first_user.py
```

Скрипт:

- создаёт базу `data/app.db` (если её ещё нет);
- проверяет, есть ли уже пользователи;
- если пользователь уже существует — выводит сообщение и завершает работу;
- если нет — спрашивает `email`, `имя`, `пароль`, `телефон` и создаёт первого суперпользователя (`role="root"`, `is_admin=True`). Этот пользователь не может быть понижен/деактивирован другими администраторами.

## Архитектура и API ядра

- Слои:
  - `core/` — бизнес-логика, не импортирует `cli`/`ui`
  - `cli/cli.py` — локальный CLI, импортирует `core` напрямую
  - `cli/cli_api_client.py` — API-клиент, работает через HTTP API
  - `ui/` — GUI (Streamlit), работает через HTTP API
  - `api/` — REST API (FastAPI), импортирует `core`, централизованный сервис скачивания

- **Централизованная система скачивания:**
  - Все новые скачивания проходят через FastAPI + TaskManager
  - Единый лимит параллельных загрузок (`MAX_CONCURRENT_DOWNLOADS`)
  - Единая очередь задач
  - `cli/cli.py` остаётся для локальной работы (напрямую с `core`)
  - `cli/cli_api_client.py` и `ui/ui_app.py` работают через HTTP API

- API:
```python
def download_video(
    url: str,
    output_path: str = ".",
    progress_callback: Callable[[float], None] | None = None,
    progress_info_callback: Callable[[dict], None] | None = None,
    cookies_path: str | None = None,
    format: str | None = None,
    audio_only: bool = False,
    subtitle_lang: str | None = None,
) -> str:
    ...
```
- Возвращает путь к загруженному файлу.
- `progress_callback`: проценты (0..100)
- `progress_info_callback`: детали (speed, downloaded_bytes, total_bytes)
- `cookies_path`: путь к cookies.txt (Netscape)

Дополнительно для UI используется анализ перед загрузкой:

```python
def analyze_video(
    url: str,
    cookies_path: str | None = None,
) -> tuple[dict, list[str], list[str]]:
    ...
```
- Возвращает кортеж:
  - `info`: метаданные ролика от yt-dlp (включая `formats`),
  - `qualities`: список удобных меток качеств (например, `'2160p'`, `'1080p'`, …, `'audio only'`),
  - `subtitle_langs`: доступные языки субтитров (коды вроде `['en', 'ru', ...]`).

## REST API

### Запуск сервера

Локальный запуск из корня проекта (после установки зависимостей):

```bash
uvicorn api.api_main:app --reload --host 0.0.0.0 --port 8000
```

Документация (FastAPI):
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

По умолчанию CORS открыт для всех источников (для удобства разработки).

### Эндпоинты

- GET `/health`
  - Ответ: `{"status": "ok"}`

- GET `/formats?url=<HttpUrl>&cookies_path=<str>`
  - Возвращает подробный `info` от yt-dlp (включая отфильтрованные `formats`).
  - Параметры:
    - `url` (обязательный) — URL видео для анализа
    - `cookies_path` (опциональный) — путь к cookies.txt (Netscape формат)
  - Коды ошибок: `400` (невалидный URL), `422` (ошибка извлечения форматов).

- GET `/analyze?url=<HttpUrl>&cookies_path=<str>`
  - Возвращает структурированные данные для UI/CLI:
    ```json
    {
      "info": {...},
      "qualities": ["2160p", "1080p", "720p", "audio only"],
      "subtitle_langs": ["en", "ru", ...]
    }
    ```
  - Параметры:
    - `url` (обязательный) — URL видео для анализа
    - `cookies_path` (опциональный) — путь к cookies.txt (Netscape формат)
  - Коды ошибок: `400` (невалидный URL), `422` (ошибка анализа).

- POST `/downloads`
  - Тело запроса (JSON):
    ```json
    {
      "url": "https://www.youtube.com/watch?v=...",
      "format": "bv*[height<=1080]+ba/best[height<=1080]",
      "audio_only": false,
      "cookies_path": "/path/to/cookies.txt",
      "subtitle_lang": "ru"
    }
    ```
  - Параметры:
    - `url` (обязательный) — URL видео для скачивания
    - `format` (опциональный) — селектор формата или конкретный format_id (игнорируется если `audio_only=true`)
    - `audio_only` (опциональный, по умолчанию `false`) — загрузить только аудио
    - `cookies_path` (опциональный) — путь к cookies.txt (Netscape формат)
    - `subtitle_lang` (опциональный) — код языка субтитров для встраивания (например, `"ru"`, `"en"`)
  - Примечания:
    - Если `audio_only=true`, параметр `format` игнорируется.
    - Если указан `format` как конкретный format_id (не селектор), API проверяет его доступность; при недоступности вернёт `422 format_unavailable`.
    - Селекторы формата (содержат `*`, `+`, `[`, `]`, `/`) валидируются yt-dlp автоматически.
  - Ответ: `201 Created`
    ```json
    { "id": "TASK_UUID" }
    ```
  - Возможные ошибки: `400` (валидация), `422` (format_unavailable), `429` (слишком много активных загрузок, зависит от стратегии), `500`.

- GET `/downloads`
  - Возвращает список задач. Элемент:
    ```json
    {
      "id": "TASK_UUID",
      "url": "https://…",
      "state": "queued|running|completed|failed|cancelled",
      "progress_percent": 42.0,
      "bytes_downloaded": 123456,
      "total_bytes": 999999,
      "speed_bps": 123456.7,
      "eta_s": 12.3,
      "elapsed_s": 45.6,
      "filename": "My Video [abc123].mp4",
      "error": null
    }
    ```

- GET `/downloads/{task_id}`
  - Возвращает состояние конкретной задачи в таком же формате, как выше.

- DELETE `/downloads/{task_id}`
  - Отменяет задачу в состояниях `queued|running`.
  - Ответ: `204 No Content` (тело: `{"status": "cancelled"}`).
  - Ошибки: `404` (нет задачи), `409` (нельзя отменить в текущем состоянии).

- GET `/downloads/{task_id}/file`
  - Возвращает скачанный файл (Content-Disposition с именем).
  - Ошибки: `409 file_not_ready`, `404 task_not_found`, `500 file_missing`.

### Примеры cURL

```bash
# Проверка здоровья
curl http://localhost:8000/health

# Получить доступные форматы
curl -G "http://localhost:8000/formats" --data-urlencode "url=https://www.youtube.com/watch?v=VIDEO_ID"

# Старт загрузки (видео+аудио с конкретным format-id или их суммой)
curl -X POST "http://localhost:8000/downloads" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID","format":"136+140","audio_only":false}'

# Старт загрузки (только аудио)
curl -X POST "http://localhost:8000/downloads" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID","audio_only":true}'

# Список задач
curl http://localhost:8000/downloads

# Статус задачи
curl http://localhost:8000/downloads/TASK_UUID

# Скачать итоговый файл
curl -OJ http://localhost:8000/downloads/TASK_UUID/file

# Отменить задачу
curl -X DELETE http://localhost:8000/downloads/TASK_UUID
```

### Переменные окружения

- `DOWNLOADS_DIR` (по умолчанию `downloads`) — куда сохранять файлы.
- `MAX_CONCURRENT_DOWNLOADS` (по умолчанию `2`) — максимальное число одновременных загрузок.
- `PROGRESS_UPDATE_INTERVAL_MS` (по умолчанию `500`) — частота обновления прогресса в API.
- `CLEANUP_INTERVAL_MIN` (по умолчанию `10`) — как часто запускать фоновую очистку завершённых задач.
- `DOWNLOAD_TTL_HOURS` (по умолчанию `24`) — TTL для хранения скачанных файлов и записей задач (если `PERSIST_DOWNLOADS=false`).
- `PERSIST_DOWNLOADS` (`false`/`true`) — если `true`, файлы не удаляются автоматически по TTL.
- `QUEUE_STRATEGY` (`enqueue`/`reject`; по умолчанию `enqueue`) — поведение при превышении лимита параллельных загрузок.

Примечания:
- Прогресс обновляется с шагом `PROGRESS_UPDATE_INTERVAL_MS`, поэтому при частом опросе клиента значения могут меняться не на каждый запрос.
- Если `QUEUE_STRATEGY=reject`, при попытке стартовать новую загрузку сверх лимита API вернёт `429` с `max_concurrent`.
- CORS открыт ко всем доменам — при необходимости ограничьте в `api.api_main`.

### Интеграционные подсказки

- Для выбора `format` получите список доступных format-id через `/formats` (поле `formats`) и подберите подходящую комбинацию `video+audio`.
- Для аудио используйте `audio_only=true` — сервер сам выберет надёжный аудиоформат.
- Рекомендуется поллинг `/downloads/{id}` до `state=completed`, затем скачивание `/downloads/{id}/file`.

## FAQ/Траблшутинг

- 403 / “Failed to parse JSON”: используйте актуальные cookies, попробуйте другой аккаунт/регион.
- “ERROR: …” дублируется: в CLI это подавлено, если видите — проверьте запуск из корня проекта.
- yt-dlp устарел/сломался: обновите `yt-dlp` в venv (`pip install -U yt-dlp`) или пересоберите Docker.
- UI не открывается/порт занят: запустите на другом порту, например `streamlit run ui/ui_app.py --server.port=8502` и откройте `http://localhost:8502`.

## Лицензия

MIT
