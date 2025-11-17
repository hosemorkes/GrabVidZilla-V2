# Базовый образ с Python 3.11+
FROM python:3.11-slim

# Базовые переменные окружения для удобной отладки и меньшего мусора
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=UTF-8 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Установка ffmpeg (необходим для yt-dlp)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Рабочая директория
WORKDIR /app

# Используем /app как домашнюю директорию, чтобы UI и CLI сохраняли файлы в /app/Downloads
ENV HOME=/app

# Подготовим стандартные каталоги внутри контейнера
RUN mkdir -p /app/Downloads /data /app/tools

# Копирование файлов зависимостей
COPY requirements.txt .

# Установка зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода приложения
COPY . .

# Объявим точки монтирования (необязательно, но удобно как подсказка)
VOLUME ["/app/Downloads", "/data", "/app/tools"]

# Точка входа: по умолчанию запускает CLI (без аргументов откроется меню)
ENTRYPOINT ["python", "-m", "cli.cli"]

