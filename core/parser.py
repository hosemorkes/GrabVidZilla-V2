"""
HTML parser helpers for GrabVidZilla.

Этот модуль содержит функции для поиска медиа-ссылок на веб-страницах:
- HLS-потоки (m3u8);
- обычные видеофайлы (mp4, webm, и т.д.).

Важно:
- модуль относится к слою core и не должен выполнять вывод в CLI/UI;
- ошибки сообщаются через ValueError/RuntimeError с короткими текстами,
  форматирование сообщений остаётся на уровне CLI/UI.
"""

from __future__ import annotations

from http.cookiejar import MozillaCookieJar
from typing import Tuple, List
from urllib.parse import urljoin, urlparse, parse_qs
import re

import requests
from bs4 import BeautifulSoup  # type: ignore[import]

from core.downloader import _clean_yt_dlp_error_message, _is_hls_m3u8_url


def _is_direct_video_url(url: str) -> bool:
    """Возвращает True, если URL выглядит как прямая ссылка на видеофайл.

    Мы проверяем только расширение пути: .mp4, .webm, .mkv, .mov, .avi, .flv.
    """
    if not isinstance(url, str) or not url:
        return False
    lower = url.lower()
    direct_video_exts = (".mp4", ".webm", ".mkv", ".mov", ".avi", ".flv")
    return lower.endswith(direct_video_exts)


def _build_headers() -> dict:
    """Возвращает набор HTTP-заголовков, имитирующих обычный браузер."""
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    }


def _load_cookies(cookies_path: str | None) -> MozillaCookieJar | None:
    """Пробует загрузить cookies из файла Netscape-формата.

    Любые ошибки чтения cookies не должны ломать общую логику,
    поэтому в случае ошибки возвращаем None.
    """
    if not cookies_path:
        return None
    try:
        jar = MozillaCookieJar()
        jar.load(cookies_path, ignore_discard=True, ignore_expires=True)
        return jar
    except Exception:
        return None


def _normalize_url(url: str) -> str:
    """Простая нормализация URL (обрезка пробелов).

    Здесь мы не применяем YouTube-специфичные преобразования, чтобы не смешивать
    обязанности с модулем downloader.
    """
    return url.strip()


def find_media_urls(
    url: str,
    cookies_path: str | None = None,
) -> Tuple[List[str], List[str]]:
    """Ищет на странице ссылки на HLS-потоки (m3u8) и обычные видеофайлы.

    Args:
        url: URL веб-страницы, которая может содержать ссылки на потоки/файлы.
        cookies_path: Путь к cookies.txt (формат Netscape) для доступа
            к приватному/региональному контенту при необходимости.

    Returns:
        Кортеж (hls_urls, file_urls):
            hls_urls: список абсолютных ссылок на HLS-потоки (.m3u8);
            file_urls: список абсолютных ссылок на обычные видеофайлы.

    Raises:
        ValueError: если URL некорректен.
        RuntimeError: если не удалось загрузить или разобрать страницу.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL должен быть непустой строкой.")

    page_url = _normalize_url(url)

    # Простая проверка схемы URL (http/https).
    parsed = urlparse(page_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL должен начинаться с http:// или https://")

    # Если нам сразу дали прямую ссылку на m3u8 или файл — вернём её как есть.
    if _is_hls_m3u8_url(page_url):
        return [page_url], []
    if _is_direct_video_url(page_url):
        return [], [page_url]

    # Загружаем HTML-страницу.
    try:
        headers = _build_headers()
        cookies = _load_cookies(cookies_path)
        resp = requests.get(page_url, headers=headers, cookies=cookies, timeout=20)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        clean = _clean_yt_dlp_error_message(str(exc))
        raise RuntimeError(f"Не удалось загрузить страницу для анализа: {clean}") from exc

    # Разбор HTML через BeautifulSoup.
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:
        raise RuntimeError(f"Не удалось разобрать HTML страницы: {exc}") from exc

    hls_urls: list[str] = []
    file_urls: list[str] = []
    seen_hls: set[str] = set()
    seen_files: set[str] = set()

    def _add_candidate(raw: str | None) -> None:
        """Преобразует относительный URL в абсолютный и добавляет в нужный список."""
        if not raw:
            return
        candidate_raw = raw

        # Некоторые сайты (например, 1fanserials) используют промежуточный player-URL
        # вида https://site/player/?file=https://cdn/.../hls.m3u8&poster=...
        # В таком случае нам нужен именно URL из параметра file, а не страница-плеер.
        try:
            parsed = urlparse(candidate_raw)
            qs = parse_qs(parsed.query or "")
            file_vals = qs.get("file")
            if file_vals:
                file_url = file_vals[0]
                if isinstance(file_url, str) and file_url:
                    candidate_raw = file_url
        except Exception:
            # Любые ошибки парсинга этого уровня не критичны — используем исходное значение.
            pass

        candidate = urljoin(page_url, candidate_raw)
        if _is_hls_m3u8_url(candidate):
            if candidate not in seen_hls:
                seen_hls.add(candidate)
                hls_urls.append(candidate)
        elif _is_direct_video_url(candidate):
            if candidate not in seen_files:
                seen_files.add(candidate)
                file_urls.append(candidate)

    # Теги <source>, <video>, <audio>
    for tag in soup.find_all(["source", "video", "audio"]):
        _add_candidate(tag.get("src"))

    # Теги <a>, <link>, <iframe>
    for tag in soup.find_all(["a", "link", "iframe"]):
        _add_candidate(tag.get("href") or tag.get("src"))

    # В некоторых плеерах ссылки могут храниться в data-* атрибутах.
    for tag in soup.find_all(True):
        for attr_name, attr_value in tag.attrs.items():
            if not isinstance(attr_value, str):
                continue
            if attr_name.startswith("data-") and any(
                ext in attr_value.lower()
                for ext in (".m3u8", ".mp4", ".webm", ".mkv", ".mov", ".avi", ".flv")
            ):
                _add_candidate(attr_value)

    # Дополнительный поиск по «сырому» HTML: ссылки могут находиться внутри JS/JSON.
    # Ищем строки вида "https://...m3u8" или "https://...mp4" и т.п.
    url_pattern = re.compile(
        r"""["'](https?://[^\s'"]+?\.(?:m3u8|mp4|webm|mkv|mov|avi|flv)(?:\?[^\s'"]*)?)["']""",
        re.IGNORECASE,
    )
    for match in url_pattern.finditer(html):
        candidate_url = match.group(1)
        _add_candidate(candidate_url)

    return hls_urls, file_urls
