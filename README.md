# GrabVidZilla

Кроссплатформенный инструмент для скачивания видео с различных платформ (YouTube, VK, Instagram, TikTok и др.).

## Возможности

- 🎥 Загрузка видео с множества платформ (через yt-dlp)
- 💻 CLI интерфейс (click + rich): прогресс-бар, скорость скачивания, цветные сообщения и эмодзи
  - Локальный CLI (`cli/cli.py`) — работает напрямую с ядром
  - API-клиент (`cli/cli_api_client.py`) — работает через HTTP API
- 🖥️ Графический интерфейс (Streamlit): анализ форматов, выбор качества и субтитров, прогресс и кнопка скачивания файла в браузере (работает через HTTP API)
- 🌐 REST API (FastAPI) + Worker: API управляет задачами (CRUD), отдельный Worker-процесс выполняет скачивание. Общение через общую SQLite БД
- 🔎 Поиск видео на веб‑страницах в CLI (HLS m3u8 и прямые ссылки на файлы) с выбором перевода и качества, Playwright fallback для динамических сайтов
- 🧾 Проверка целостности: вывод SHA-256 после каждой загрузки (CLI, API, UI)
- 🧩 Поддержка cookies.txt (Netscape) для приватных/региональных видео (VK/YouTube и др.)
- 🐳 Docker-образ (включает ffmpeg)

## Требования

- Python 3.11+
- ffmpeg (в системе) — для локального запуска; в Docker ставится автоматически
- Рекомендуется актуальный `yt-dlp` (в проекте зафиксирован в `requirements.txt`)
- Используемые библиотеки (Python): `yt-dlp`, `streamlit`, `click`, `rich`, `requests`, `beautifulsoup4`, `playwright`, `aiogram`, `httpx`, `python-dotenv` (см. `requirements.txt`)

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
python -m playwright install chromium
# Динамический парсинг через Playwright

Некоторые сайты отдают ссылки на медиаконтент только после выполнения JavaScript на странице. Для таких случаев GrabVidZilla может использовать Chromium через Playwright.

- Убедитесь, что установлен сам пакет (`playwright`) и браузерные движки:

  ```bash
  pip install -r requirements.txt
  python -m playwright install chromium
  ```

- Дополнительные зависимости для Linux:

  ```bash
  python -m playwright install --with-deps chromium
  ```

- Переменные окружения для прокси (опционально):
  - `GVZ_BROWSER_PROXY_SERVER` — адрес прокси вида `http://host:port` или `socks5://host:port`.
  - `GVZ_BROWSER_PROXY_USERNAME`, `GVZ_BROWSER_PROXY_PASSWORD` — учётные данные, если требуются.

Эти параметры используются как значения по умолчанию при запуске браузерного парсера в CLI, API и UI. В интерфейсах также можно задать прокси вручную.
- При необходимости выбрать определённый перевод/озвучку используйте меню CLI/UI или передайте `translation_hash` в REST API — парсер автоматически переключит нужный плеер и вернёт обновлённые ссылки с указанием качества.
- Если сайт отвечает некорректным SSL-сертификатом, можно временно отключить проверку, установив `GVZ_ALLOW_INSECURE_SSL=1` (или `true`/`yes`). Значение применяется как для статического `requests`-парсера, так и для Playwright.

3) Добавьте ffmpeg в PATH (если не установлен системно):
- Windows (пример): добавьте путь к бинарнику в PATH текущей сессии.
- Или используйте Docker (ниже) — там ffmpeg уже ставится.

## Использование: UI (Streamlit)

**Важно:** UI работает через HTTP API. Перед запуском UI необходимо запустить API сервер и Worker (см. раздел REST API).

Локальный запуск из корня проекта (после установки зависимостей):

```bash
# 1. Сначала запустите API сервер (в отдельном терминале)
uvicorn api.api_main:app --reload --host 0.0.0.0 --port 8000

# 2. Запустите Worker (в отдельном терминале)
python -m worker.worker_main

# 3. Затем запустите UI (в другом терминале)
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
  - 4. Найти видео на странице (статический парсер + Playwright fallback, выбор перевода и качества перед скачиванием)
  - 5. Поиск видео на специфичных сайтах (автоопределение site-specific адаптера, выбор перевода/качества)
  - 0. Выход

- Прямые примеры:
```bash
python -m cli.cli "https://youtu.be/..." -o ".\Downloads"
python -m cli.cli "https://vkvideo.ru/..." --cookies tools\cookies.txt
```

- Отображаем:
  - Прогресс (0..100%)
  - Текущую скорость (Б/с, КБ/с, МБ/с)
  - По завершении — имя файла, время скачивания, размер файла и SHA-256 контрольную сумму

- Путь по умолчанию:
  - Меню: `Downloads` в корне проекта
  - CLI: `-o/--output`, если не задано — также `Downloads`

### API-клиент (работает через HTTP API)

**Важно:** Перед запуском API-клиента необходимо запустить API сервер и Worker (см. раздел REST API).

Запуск из корня проекта:
```bash
python -m cli.cli_api_client
```

- Меню аналогично локальному CLI, но все операции выполняются через REST API:
  - 1. Скачать видео (через `POST /downloads` и отслеживание прогресса)
  - 2. help
  - 3. Загрузить cookies (объединение с существующими в `tools/cookies.txt`)
  - 4. Найти видео на странице (через REST `/media`, с переводами, качествами и Playwright fallback)
  - 5. Поиск видео на специфичных сайтах (через REST `/media` с автоопределением адаптера)
  - 0. Выход

- Переменные окружения:
  - `GVZ_API_URL` — URL API сервера (по умолчанию `http://localhost:8000`)
  - `GVZ_ALLOW_INSECURE_SSL` — если `1/true/yes`, парсер пропускает проверку SSL (использовать только для доверенных сайтов)

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
  - По завершении — имя файла, время скачивания, размер файла и SHA-256 контрольную сумму

- Преимущества API-клиента:
  - Единый лимит параллельных загрузок (`MAX_CONCURRENT_DOWNLOADS`)
  - Единая очередь задач
  - Можно использовать несколько клиентов одновременно

### Скачивание нескольких видео (batch-режим)

Пункт «Скачать видео» поддерживает три формата ввода:

**Одна ссылка** — обычный режим с анализом форматов и выбором качества:
```
https://youtube.com/watch?v=xxx
```

**Несколько ссылок через пробел:**
```
https://youtube.com/watch?v=aaa https://vk.com/video123
```

**Файл со списком URL** (введите путь к `.txt` файлу):
```
urls.txt
```

Формат файла `urls.txt`:
```
# комментарии игнорируются
https://youtube.com/watch?v=aaa
https://tiktok.com/@user/video/bbb
https://vk.com/video-123456_789
```

При batch-режиме прогресс всех задач отображается в живой таблице с колонками:
`# | Сайт | Статус | Прогресс | Скорость | Файл`

Все настройки формата (cookies, качество) применяются ко всем URL одинаково.

Работает в обоих режимах:
- `cli/cli.py` — локально напрямую (задачи выполняются **последовательно**)
- `cli/cli_api_client.py` — через HTTP API (задачи отправляются в Worker и выполняются **параллельно**)

#### Поиск видео на странице (пункт 4 меню CLI)

- В пункте «4. Найти видео на странице» вы можете ввести URL обычной веб‑страницы:
  - `core/parser.py` сначала пытается статически разобрать HTML (requests + BeautifulSoup) и найти ссылки на:
    - HLS-потоки (`*.m3u8`, включая ссылки, спрятанные в player‑URL вроде `...?file=...hls.m3u8`),
    - прямые видеофайлы (`.mp4`, `.webm` и т.д.).
  - Если статический разбор не дал ссылок, при включённом флаге `--use-browser-parser` (или настроенном по умолчанию fallback) задействуется Playwright: запускается Chromium headless, нужный перевод активируется по `data-sound-hash`, собираются сетевые запросы и HTML.
  - Перед выводом ссылок CLI предлагает выбрать перевод озвучки (если сайт предоставляет варианты) и качество потока.
  - Итоговый список содержит ссылки с пометками «Перевод» и «Качество», после скачивания выводится SHA-256.
  - Далее используется тот же движок `download_video`, что и для обычных URL (поддерживается прогресс, скорость, переименование файла и т.п.).

- Ограничения:
  - Даже при использовании браузерного парсера сайт может скрывать ссылки за DRM или генерацией токенов — тогда потребуется вручную получить прямой URL.
  - Для сайтов с некорректными сертификатами можно временно отключить проверку SSL через `GVZ_ALLOW_INSECURE_SSL=1` (использовать только при доверенном источнике).

#### Поиск видео на специфичных сайтах (пункт 5 меню CLI)

- В пункте «5. Поиск видео на специфичных сайтах» система автоматически определяет подходящий site-specific адаптер для заданного URL.
- Архитектура site-specific адаптеров:
  - Базовый класс `SiteParserAdapter` (`core/site_parsers/base.py`) определяет интерфейс:
    - `can_handle(url) -> bool` — проверка, может ли адаптер обработать URL
    - `parse(url, cookies_path, translation_hash, proxy) -> tuple[...]` — парсинг страницы и извлечение медиа-ссылок
  - Регистрация адаптеров через `core/site_parsers/__init__.py`:
    - `register_adapter(adapter)` — регистрация нового адаптера
    - `get_adapter_for_url(url)` — автоматическое определение подходящего адаптера
  - Примеры адаптеров:
    - `FanserialsAdapter` (`core/site_parsers/fanserials.py`) — для сайтов fanserials (1fanserials.ru и др.):
      - Определяет сайт по наличию "fanserials" в домене
      - Извлекает доступные переводы из элементов с `data-sound-hash`
      - Использует браузерный парсер для извлечения ссылок из PlayerJS плеера
      - Поддерживает выбор перевода через `translation_hash`
- Процесс работы:
  - CLI автоматически определяет подходящий адаптер по URL
  - Если адаптер найден, используется его специализированная логика парсинга
  - Если адаптер не найден, выводится сообщение об отсутствии поддержки
  - После извлечения ссылок предлагается выбор перевода и качества (если доступны)
- Преимущества:
  - Расширяемость: легко добавить новые адаптеры для специфичных сайтов
  - Специализированная логика: каждый адаптер знает особенности своего сайта
  - Автоматическое определение: не нужно указывать тип сайта вручную

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

В репозитории есть файл `docker-compose.yml`, который упрощает запуск CLI, UI, API и Worker, а также заранее создаёт общую Docker-сеть для интеграции с другими контейнерами.

- **Тома и папки:**
  - Локальная папка `Downloads` монтируется в контейнер как `/app/Downloads` — сюда сохраняются все загруженные видео.
  - Локальная папка `tools` монтируется в контейнер как `/app/tools` — внутри ожидается файл `cookies.txt` для авторизации на сайтах (формат Netscape).
  - Локальная папка `data` монтируется в контейнер как `/app/data` (сервис `grabvidzilla-api`) — SQLite-база `app.db` с пользователями и задачами скачивания переживает пересборку контейнера.
  - Если папок `Downloads`, `tools` или `data` нет, создайте их рядом с `docker-compose.yml`.

- **Сеть:**
  - В `docker-compose.yml` объявлена пользовательская сеть `grabvidzilla-net`.
  - Все четыре сервиса (`grabvidzilla-cli`, `grabvidzilla-ui`, `grabvidzilla-api`, `grabvidzilla-worker`) подключены к этой сети.
  - Любые другие контейнеры, запущенные через docker-compose или вручную с опцией `--network grabvidzilla-net`, смогут обращаться к GrabVidZilla по имени сервиса.

- **Запуск CLI (интерактивное меню, локальный режим):**
  - Собрать и запустить контейнер с меню (работает напрямую с `core`):
    ```bash
    docker compose up grabvidzilla-cli
    ```
  - Файлы будут сохраняться в локальную папку `Downloads` рядом с проектом.

- **Запуск CLI через API (в контейнере):**
  - Если контейнер `grabvidzilla-cli` уже запущен (вместе с API и Worker), можно использовать CLI-клиент, который работает через HTTP API:
    ```bash
    docker exec -it grabvidzilla-cli python -m cli.cli_api_client
    ```
  - В контейнере CLI уже задана переменная `GVZ_API_URL=http://grabvidzilla-api:8000`, поэтому клиент автоматически подключится к API-сервису.
  - Преимущества: единая очередь задач, лимит параллельных загрузок, возможность использовать несколько клиентов одновременно.
  - **Batch через файл urls.txt в контейнере:** файл со списком URL должен быть смонтирован внутрь контейнера через volume. Добавьте в `docker-compose.yml` в секцию `grabvidzilla-cli → volumes`:
    ```yaml
    - ./urls.txt:/app/urls.txt
    ```
    После этого в меню CLI введите путь `/app/urls.txt`.

- **Запуск API + Worker (рекомендуется):**
  - Запустить API сервер и Worker вместе:
    ```bash
    docker compose up grabvidzilla-api grabvidzilla-worker
    ```
  - API будет доступен по адресу `http://localhost:8000`.
  - Worker автоматически начнёт забирать задачи из БД и скачивать.
  - SQLite-база (`./data/app.db`) и папка загрузок (`./Downloads`) общие между API и Worker.

- **Запуск UI (Streamlit):**
  - Запустить UI (требуется работающий API + Worker):
    ```bash
    docker compose up grabvidzilla-ui grabvidzilla-api grabvidzilla-worker
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

5) Запустите CLI или API + Worker + UI:

- CLI меню (локальная работа без API):

  ```bash
  python -m cli.cli
  ```

- API + Worker + UI:

  ```bash
  # Терминал 1: API
  uvicorn api.api_main:app --host 0.0.0.0 --port 8000 &

  # Терминал 2: Worker
  python -m worker.worker_main &

  # Терминал 3: UI
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

6) Запуск API + Worker + UI на сервере:

```bash
sudo docker compose up -d grabvidzilla-api grabvidzilla-worker grabvidzilla-ui
```

После этого UI будет доступен по адресу `http://<IP_сервера>:8501`, API — `http://<IP_сервера>:8000`. Все загруженные файлы также будут складываться в `/opt/grabvidzilla/Downloads`.

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
sudo docker compose up -d grabvidzilla-api grabvidzilla-worker grabvidzilla-ui
```

После пересборки и перезапуска сервисов `docker compose` автоматически подхватит обновлённый образ.

## Структура проекта

```
grabvidzilla/
├── core/                    # Бизнес-логика загрузки видео и пользователей
│   ├── downloader.py        # Загрузка видео через yt-dlp
│   ├── parser.py            # Статический парсер HTML (requests + BeautifulSoup)
│   ├── browser_parser.py   # Браузерный парсер (Playwright Chromium)
│   ├── site_parsers/        # Site-specific адаптеры для специфичных сайтов
│   │   ├── __init__.py      # Регистрация и автоопределение адаптеров
│   │   ├── base.py          # Базовый класс SiteParserAdapter
│   │   └── fanserials.py   # Адаптер для fanserials (PlayerJS)
│   ├── db.py                # SQLite база данных (модели User и Download, WAL-режим)
│   ├── errors.py            # Классификация ошибок скачивания (17 категорий)
│   └── auth.py              # Аутентификация и управление пользователями
├── cli/                     # Командная строка (click + rich)
│   ├── cli.py              # Локальный CLI (работает напрямую с core)
│   └── cli_api_client.py   # API-клиент (работает через HTTP API)
├── ui/                      # Графический интерфейс (Streamlit)
│   ├── ui_app.py           # Основной UI
│   └── ui_auth.py          # Аутентификация в UI
├── api/                     # REST API (FastAPI)
│   ├── api_main.py         # FastAPI приложение
│   └── api_service.py      # TaskManager: CRUD задач в БД, TTL-очистка
├── worker/                  # Worker-процесс (скачивание)
│   ├── __main__.py         # Запуск через python -m worker
│   └── worker_main.py     # Polling БД, скачивание, прогресс, graceful shutdown
├── bot/                     # Telegram-бот
│   └── bot_main.py         # aiogram 3.x, Long Polling, работает через HTTP API
├── tools/                   # Внешние утилиты, cookies.txt, ffmpeg
├── Downloads/              # Папка загрузок по умолчанию (CLI и меню)
├── data/                   # SQLite база данных (app.db)
├── tests/                  # Тесты
└── scripts/                # Утилиты разработки
```

### Аутентификация и база данных пользователей

- Модуль БД: `core/db.py`
  - SQLite-файл по умолчанию: `data/app.db` (создаётся автоматически при первом запуске).
  - WAL-режим включён для безопасной работы из нескольких процессов (API + Worker).
  - Содержит:
    - `engine` — подключение к SQLite (с WAL-pragma);
    - `SessionLocal` — фабрика сессий;
    - `Base` — базовый класс для ORM-моделей;
    - `Download` — ORM-модель задачи скачивания (таблица `downloads`, см. ниже);
    - `init_db()` — создаёт все таблицы + запускает автомиграцию для новых колонок.
- Модель задачи скачивания: `Download` (`core/db.py`, таблица `downloads`)
  - Хранит полный жизненный цикл задачи — от создания (queued) до завершения (completed / error / cancelled).
  - Поля:
    - `id: String` (PK) — UUID задачи;
    - `url: String` — исходная ссылка на видео;
    - `status: String` — текущее состояние: `queued` → `downloading` → `completed` / `error` / `cancelled`;
    - `progress: Float` — процент загрузки (0.0 – 100.0);
    - `speed: String | None` — текущая скорость загрузки (например, `"1.2 MiB/s"`);
    - `eta: String | None` — оставшееся время (например, `"00:01:23"`);
    - `output_path: String | None` — абсолютный путь к скачанному файлу;
    - `filename: String | None` — только имя файла (без пути);
    - `file_size: Integer | None` — размер файла в байтах;
    - `sha256: String | None` — контрольная сумма SHA-256;
    - `format_id: String | None` — запрошенный формат (селектор или format_id);
    - `audio_only: Boolean` — загрузка только аудио;
    - `subtitle_lang: String | None` — язык субтитров для встраивания;
    - `cookies_path: String | None` — путь к cookies.txt;
    - `error_message: String | None` — читаемый текст ошибки для пользователя/UI;
    - `error_type: String | None` — категория ошибки (16 типов: `interrupted`, `not_found`, `geo_blocked`, `private_video`, `removed_video`, `live_stream`, `format_unavailable`, `download_timeout`, `network_error`, `rate_limited`, `auth_required`, `unsupported_site`, `disk_full`, `ffmpeg_error`, `corrupted_download`, `cancelled`, `unknown`);
    - `error_details: Text | None` — полный traceback/stderr (для отладки);
    - `created_at: DateTime` — время создания задачи;
    - `started_at: DateTime | None` — время начала скачивания;
    - `finished_at: DateTime | None` — время завершения (успех/ошибка/отмена);
    - `ttl_expires_at: DateTime | None` — когда запись и файл можно автоматически удалить;
    - `cancellation_requested: Boolean` — флаг запроса отмены (API ставит `True`, Worker проверяет в progress_callback);
    - `webhook_url: String | None` — URL для POST-уведомления при завершении задачи;
    - `webhook_sent: Boolean` — флаг успешной отправки webhook;
    - `telegram_chat_id: String | None` — ID чата Telegram для уведомления через Bot API;
    - `convert_to_mp4: Boolean` — если `True`, это задача конвертации: Worker вызовет `convert_to_mp4()` вместо `download_video()`, `output_path` указывает на исходный файл.
  - Схема статусов:
    ```
    queued → downloading → completed
                        ↘ error
    queued → cancelled
    downloading → cancelled
    ```
  - При перезапуске Worker: задачи в `downloading` автоматически помечаются как `error` с `error_type = "interrupted"`.
  - TTL: после завершения задачи устанавливается `ttl_expires_at = now + DOWNLOAD_TTL_HOURS`. Фоновый поток очистки удаляет записи и файлы по истечении TTL.
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
    - `core/parser.py` — статический парсер HTML (requests + BeautifulSoup)
    - `core/browser_parser.py` — браузерный парсер (Playwright Chromium) для динамического контента
    - `core/site_parsers/` — site-specific адаптеры для специфичных сайтов (расширяемая архитектура)
  - `cli/cli.py` — локальный CLI, импортирует `core` напрямую
  - `cli/cli_api_client.py` — API-клиент, работает через HTTP API
  - `ui/` — GUI (Streamlit), работает через HTTP API
  - `api/` — REST API (FastAPI), импортирует `core`. Только CRUD задач в БД + TTL-очистка. **Не скачивает.**
  - `worker/` — Worker-процесс, импортирует `core`. Забирает задачи из БД и выполняет скачивание.

- **Архитектура API + Worker (Фаза 3):**
  - API и Worker — отдельные процессы (контейнеры). API только управляет задачами (CRUD по БД), Worker — только скачивает.
  - Общение между ними — исключительно через общую SQLite БД (`data/app.db`) + общий Docker volume для файлов загрузок (`Downloads/`).
  - SQLite работает в WAL-режиме для безопасного одновременного доступа из нескольких процессов.
  - `POST /downloads` → API создаёт запись `queued` в БД → Worker забирает при polling → обновляет прогресс в БД → API читает из БД.
  - `POST /downloads/{id}/convert` → API создаёт задачу с `convert_to_mp4=True` и `output_path` → исходный файл → Worker вызывает `convert_to_mp4()` вместо `download_video()` → исходный файл удаляется после успеха.
  - Отмена: API ставит `cancellation_requested=True` в БД → Worker проверяет в progress_callback и прерывает.
  - При перезапуске Worker зависшие задачи (`downloading`) автоматически помечаются как `error(interrupted)`.
  - API можно перезапустить без потери активных скачиваний (Worker продолжает работать).
  - Завершённые задачи (completed, error, cancelled) сохраняются в истории
  - Единый лимит параллельных загрузок (`MAX_CONCURRENT_DOWNLOADS`) — контролируется Worker-ом
  - Классификация ошибок (`core/errors.py`): каждая ошибка получает `error_type` (17 категорий) и читаемый `error_message`
  - TTL-очистка (API): через `DOWNLOAD_TTL_HOURS` (по умолчанию 48ч) файл и запись автоматически удаляются
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

Парсинг веб-страниц для поиска медиа-ссылок:

```python
# Статический парсер (core/parser.py)
def find_media_urls(
    url: str,
    cookies_path: str | None = None,
    translation_hash: str | None = None,
) -> tuple[list[str], list[str], str | None, list[dict] | None, list[dict]]:
    ...
```
- Возвращает: `(hls_urls, file_urls, page_title, translations, hls_streams)`
- Использует requests + BeautifulSoup для статического парсинга HTML

```python
# Браузерный парсер (core/browser_parser.py)
def fetch_media_urls_with_browser(
    url: str,
    cookies_path: str | None = None,
    proxy: dict | None = None,
    translation_hash: str | None = None,
) -> tuple[list[str], list[str], str | None, list[dict] | None, list[dict]]:
    ...
```
- Возвращает: `(hls_urls, file_urls, page_title, translations, hls_streams)`
- Использует Playwright Chromium для динамического парсинга JavaScript-контента

```python
# Site-specific адаптеры (core/site_parsers/)
class SiteParserAdapter(ABC):
    def can_handle(self, url: str) -> bool: ...
    def parse(
        self,
        url: str,
        cookies_path: str | None = None,
        translation_hash: str | None = None,
        proxy: dict | None = None,
    ) -> tuple[list[str], list[str], str | None, list[dict] | None, list[dict]]: ...

# Автоопределение адаптера
def get_adapter_for_url(url: str) -> SiteParserAdapter | None: ...
```
- Расширяемая архитектура для специфичных сайтов
- Автоматическое определение подходящего адаптера по URL
- Примеры: `FanserialsAdapter` для fanserials с PlayerJS плеером

## REST API

### Запуск сервера

Локальный запуск из корня проекта (после установки зависимостей):

```bash
# 1. Запуск API сервера (в одном терминале)
uvicorn api.api_main:app --reload --host 0.0.0.0 --port 8000

# 2. Запуск Worker (в другом терминале)
python -m worker.worker_main
```

Worker автоматически начнёт забирать задачи из БД и скачивать.

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
      "subtitle_lang": "ru",
      "webhook_url": "http://your-server/callback",
      "telegram_chat_id": "123456789"
    }
    ```
  - Параметры:
    - `url` (обязательный) — URL видео для скачивания
    - `format` (опциональный) — селектор формата или конкретный format_id (игнорируется если `audio_only=true`)
    - `audio_only` (опциональный, по умолчанию `false`) — загрузить только аудио
    - `cookies_path` (опциональный) — путь к cookies.txt (Netscape формат)
    - `subtitle_lang` (опциональный) — код языка субтитров для встраивания (например, `"ru"`, `"en"`)
    - `webhook_url` (опциональный) — URL для POST-уведомления при завершении задачи
    - `telegram_chat_id` (опциональный) — ID чата Telegram для уведомления через Bot API
  - Примечания:
    - Если `audio_only=true`, параметр `format` игнорируется.
    - Если указан `format` как конкретный format_id (не селектор), API проверяет его доступность; при недоступности вернёт `422 format_unavailable`.
    - Селекторы формата (содержат `*`, `+`, `[`, `]`, `/`) валидируются yt-dlp автоматически.
  - Ответ: `201 Created`
    ```json
    { "id": "TASK_UUID" }
    ```
  - Примечание: после завершения задачи статусные ответы (`GET /downloads*`) содержат поле `sha256` с SHA-256 хешем файла.
  - Возможные ошибки: `400` (валидация), `422` (format_unavailable), `429` (слишком много активных загрузок, зависит от стратегии), `500`.

- GET `/downloads`
  - Возвращает список всех задач (включая историю из SQLite). Элемент:
    ```json
    {
      "id": "TASK_UUID",
      "url": "https://…",
      "status": "queued|downloading|completed|error|cancelled",
      "progress": 42.0,
      "speed": "1.2 MiB/s",
      "eta": "00:01:23",
      "filename": "My Video [abc123].mp4",
      "file_size": 999999,
      "sha256": "3f786850e387550fdab836ed7e6dc881de23001b",
      "format_id": "bv*[height<=1080]+ba/best[height<=1080]",
      "audio_only": false,
      "error_message": null,
      "error_type": null,
      "created_at": "2026-02-21T12:00:00",
      "started_at": "2026-02-21T12:00:01",
      "finished_at": "2026-02-21T12:01:30"
    }
    ```
  - При ошибке поля `error_type` и `error_message` заполняются (например, `"error_type": "not_found"`, `"error_message": "Видео не найдено"`).
  - Категории `error_type`: `interrupted`, `not_found`, `geo_blocked`, `private_video`, `removed_video`, `live_stream`, `format_unavailable`, `download_timeout`, `network_error`, `rate_limited`, `auth_required`, `invalid_url`, `unsupported_site`, `disk_full`, `ffmpeg_error`, `corrupted_download`, `cancelled`, `unknown`.

- GET `/downloads/{task_id}`
  - Возвращает состояние конкретной задачи в таком же формате, как выше.

- DELETE `/downloads/{task_id}`
  - Отменяет задачу в состояниях `queued|downloading`.
  - Ответ: `204 No Content` (тело: `{"status": "cancelled"}`).
  - Ошибки: `404` (нет задачи), `409` (нельзя отменить в текущем состоянии).

- GET `/downloads/{task_id}/file`
  - Возвращает скачанный файл (Content-Disposition с именем).
  - Ошибки: `409 file_not_ready`, `404 task_not_found`, `500 file_missing`.

- POST `/downloads/{task_id}/convert`
  - Создаёт задачу конвертации уже скачанного файла в MP4 (H.264 + AAC + faststart).
  - Worker заберёт задачу при следующем polling-цикле и вызовет ffmpeg. Исходный файл удаляется.
  - Query-параметры: `telegram_chat_id` (опционально, для уведомления).
  - Ответ: `201 Created` → `{"id": "NEW_TASK_UUID"}`
  - Ошибки: `404 task_not_found` / `404 source_file_missing`, `409 source_task_not_completed` / `409 already_mp4`.

- GET `/media`
  - Выполняет поиск медиа-ссылок (статический парсер + опциональный Playwright fallback).
  - Параметры: `url` (обязательный), `cookies_path?`, `use_browser?`, `fallback_to_browser?`, `translation_hash?`, `proxy_server?`, `proxy_username?`, `proxy_password?`.
  - Ответ содержит списки `hls_urls`, `file_urls`, `translations` (переводы), `hls_streams` (качественные варианты) и флаги `used_browser`, `static_found`.

### Примеры cURL

```bash
# Проверка здоровья
curl http://localhost:8000/health

# Получить доступные форматы
curl -G "http://localhost:8000/formats" --data-urlencode "url=https://www.youtube.com/watch?v=VIDEO_ID"

# Найти медиа-ссылки на странице с fallback в браузер и выбором перевода
curl -G "http://localhost:8000/media" \
  --data-urlencode "url=https://example.com/page" \
  --data-urlencode "fallback_to_browser=true" \
  --data-urlencode "translation_hash=#player_Onibaku"

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

# Конвертировать скачанный файл в MP4
curl -X POST "http://localhost:8000/downloads/TASK_UUID/convert"

# То же, но с уведомлением в Telegram
curl -X POST "http://localhost:8000/downloads/TASK_UUID/convert?telegram_chat_id=123456789"
```

### Переменные окружения

**API (grabvidzilla-api):**
- `DOWNLOADS_DIR` (по умолчанию `downloads`) — куда сохранять файлы.
- `MAX_CONCURRENT_DOWNLOADS` (по умолчанию `2`) — максимальное число одновременных загрузок (для стратегии `reject`).
- `CLEANUP_INTERVAL_MIN` (по умолчанию `30`) — как часто запускать фоновую очистку завершённых задач (минуты).
- `DOWNLOAD_TTL_HOURS` (по умолчанию `48`) — TTL для хранения скачанных файлов и записей задач в SQLite (если `PERSIST_DOWNLOADS=false`). По истечении TTL файл удаляется с диска, запись — из БД.
- `PERSIST_DOWNLOADS` (`false`/`true`) — если `true`, файлы не удаляются автоматически по TTL.
- `QUEUE_STRATEGY` (`enqueue`/`reject`; по умолчанию `enqueue`) — поведение при превышении лимита параллельных загрузок.
- `GVZ_ALLOW_INSECURE_SSL` (`0`/`1`, `false`/`true`, `no`/`yes`) — при включении отключает проверку SSL в статическом парсере и Playwright (использовать только для доверенных сайтов).

**Worker (grabvidzilla-worker):**
- `DOWNLOADS_DIR` (по умолчанию `Downloads`) — куда сохранять файлы (должно совпадать с API).
- `MAX_CONCURRENT_DOWNLOADS` (по умолчанию `2`) — максимальное число одновременных загрузок.
- `WORKER_POLL_INTERVAL_SEC` (по умолчанию `3`) — как часто Worker опрашивает БД на новые задачи (секунды).
- `WORKER_SHUTDOWN_TIMEOUT_SEC` (по умолчанию `30`) — максимальное время ожидания текущих задач при остановке Worker (секунды).
- `PROGRESS_UPDATE_INTERVAL_MS` (по умолчанию `1000`) — частота записи прогресса в БД (миллисекунды).
- `DOWNLOAD_TTL_HOURS` (по умолчанию `48`) — TTL для записей задач.
- `TELEGRAM_BOT_TOKEN` — токен Telegram-бота (для отправки уведомлений при завершении задач).
- `GVZ_API_URL` — внутренний адрес API.
- `GVZ_API_PUBLIC_URL` — публичный адрес API для ссылок в уведомлениях.

**Telegram-бот (grabvidzilla-tel-bot):**
- `TELEGRAM_BOT_TOKEN` — токен бота от @BotFather.
- `TELEGRAM_ALLOWED_USERS` — список user_id через запятую (whitelist). Если пусто — доступ всем.
- `GVZ_API_URL` (по умолчанию `http://localhost:8000`) — внутренний адрес API.
- `GVZ_API_PUBLIC_URL` — публичный адрес API для ссылок пользователю (может отличаться от внутреннего Docker-адреса).

Примечания:
- API только создаёт задачи в БД и читает их состояние. Worker выполняет скачивание и пишет прогресс напрямую в БД.
- Прогресс, speed и eta обновляются Worker-ом в SQLite с троттлингом (`PROGRESS_UPDATE_INTERVAL_MS`). API читает из БД при каждом запросе.
- Если `QUEUE_STRATEGY=reject`, при попытке стартовать новую загрузку сверх лимита API вернёт `429` с `max_concurrent`.
- CORS открыт ко всем доменам — при необходимости ограничьте в `api.api_main`.

### Интеграционные подсказки

- Для выбора `format` получите список доступных format-id через `/formats` (поле `formats`) и подберите подходящую комбинацию `video+audio`.
- Для аудио используйте `audio_only=true` — сервер сам выберет надёжный аудиоформат.
- Рекомендуется поллинг `/downloads/{id}` до `status=completed`, затем скачивание `/downloads/{id}/file`.

## Telegram-бот

GrabVidZilla включает Telegram-бота, который позволяет скачивать видео прямо из чата.

### Настройка

1. Получите токен бота у [@BotFather](https://t.me/BotFather).
2. Узнайте свой `user_id` (например, через [@userinfobot](https://t.me/userinfobot)).
3. Заполните `.env` файл в корне проекта:

```env
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_ALLOWED_USERS=123456789,987654321
```

### Запуск

**Через Docker Compose (рекомендуется):**

```bash
docker compose up -d grabvidzilla-api grabvidzilla-worker grabvidzilla-tel-bot
```

**Локально:**

```bash
# 1. Запустите API + Worker (в отдельных терминалах)
uvicorn api.api_main:app --host 0.0.0.0 --port 8000
python -m worker.worker_main

# 2. Запустите бота
python -m bot.bot_main
```

### Возможности

- Отправьте ссылку → бот анализирует и предлагает выбор формата (inline-кнопки)
- Прогресс скачивания отображается в реальном времени (редактирование сообщения)
- По завершении:
  - Файлы ≤ 500 MB — автоматически отправляются как документ (`send_document`) прямо в чат
  - Файлы > 500 MB — текстовая ссылка для скачивания через браузер
  - Если файл не `.mp4` (например, `.webm`) — появляется inline-кнопка **«Конвертировать в MP4»**: бот запускает конвертацию через Worker и отслеживает прогресс
- Команды: `/queue`, `/history`, `/cancel <id>`, `/help`
- `/history` — кнопки «Скачать» для файлов ≤ 500 MB, текстовые ссылки для больших
- Whitelist: только пользователи из `TELEGRAM_ALLOWED_USERS` могут использовать бота
- `python-dotenv`: бот автоматически читает `.env` при запуске (`python -m bot.bot_main`)

### Уведомления от Worker

Worker автоматически отправляет уведомление инициатору задачи в Telegram при завершении скачивания (успех или ошибка). Для этого Worker использует `TELEGRAM_BOT_TOKEN` из переменных окружения.

## Webhook-уведомления

При создании задачи скачивания можно указать `webhook_url` — Worker автоматически отправит POST-запрос на этот URL при завершении задачи.

```bash
curl -X POST "http://localhost:8000/downloads" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID","webhook_url":"http://your-server/callback"}'
```

Тело webhook-уведомления:

```json
{
  "id": "uuid",
  "status": "completed",
  "url": "https://...",
  "filename": "video.mp4",
  "file_size": 838860800,
  "error_type": null,
  "error_message": null,
  "finished_at": "2026-01-01T12:00:00"
}
```

## Интеграция с n8n

### Требования
- n8n уже запущен (отдельно или через docker-compose)
- grabvidzilla-api доступен по сети

### Подключение

1. Убедитесь что n8n и grabvidzilla находятся в одной Docker-сети:

```yaml
# В вашем n8n docker-compose или запуске добавьте сеть:
networks:
  - grabvidzilla-net

# Внешняя сеть должна быть объявлена:
networks:
  grabvidzilla-net:
    external: true
```

2. В n8n используйте базовый URL: `http://grabvidzilla-api:8000`

### Создать задачу скачивания из n8n

HTTP Request node:
- Method: `POST`
- URL: `http://grabvidzilla-api:8000/downloads`
- Body (JSON):
```json
{
  "url": "{{ $json.video_url }}",
  "format": "bestvideo+bestaudio",
  "webhook_url": "http://your-n8n:5678/webhook/grabvidzilla-done"
}
```

### Получить статус задачи

HTTP Request node:
- Method: `GET`
- URL: `http://grabvidzilla-api:8000/downloads/{{ $json.id }}`

### Получить уведомление о завершении (Webhook)

1. Создайте Webhook node в n8n (метод POST, путь `/grabvidzilla-done`)
2. Передайте URL этого webhook в поле `webhook_url` при создании задачи
3. Worker автоматически отправит POST на этот URL когда задача завершится

Тело webhook-уведомления:
```json
{
  "id": "uuid",
  "status": "completed",
  "filename": "video.mp4",
  "file_size": 838860800,
  "finished_at": "2025-01-01T12:00:00"
}
```

## Масштабирование Worker

Для запуска нескольких Worker-ов используйте `--scale`:

```bash
docker compose up -d --scale grabvidzilla-worker=3
```

Каждый Worker атомарно захватывает задачи через `_try_claim_task()` (UPDATE с проверкой статуса) — два Worker'а не возьмут одну задачу дважды. Работает безопасно с SQLite (WAL-режим) и PostgreSQL.

## FAQ/Траблшутинг

- 403 / “Failed to parse JSON”: используйте актуальные cookies, попробуйте другой аккаунт/регион.
- “ERROR: …” дублируется: в CLI это подавлено, если видите — проверьте запуск из корня проекта.
- yt-dlp устарел/сломался: обновите `yt-dlp` в venv (`pip install -U yt-dlp`) или пересоберите Docker.
- UI не открывается/порт занят: запустите на другом порту, например `streamlit run ui/ui_app.py --server.port=8502` и откройте `http://localhost:8502`.

## Лицензия

MIT
