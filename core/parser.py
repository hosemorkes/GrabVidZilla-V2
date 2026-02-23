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
from typing import Tuple, List, Optional, Dict, Set
from urllib.parse import urljoin, urlparse, parse_qs
import logging
import os
import re

import requests
from bs4 import BeautifulSoup  # type: ignore[import]

from core.downloader import _clean_yt_dlp_error_message, _is_hls_m3u8_url

# Импорт браузерного парсера для обратной совместимости
from core.browser_parser import fetch_media_urls_with_browser  # noqa: F401

logger = logging.getLogger(__name__)


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
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
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


def _extract_media_from_html(
    page_url: str,
    html: str,
    seen_hls: Optional[Set[str]] = None,
    seen_files: Optional[Set[str]] = None,
    target_hash: Optional[str] = None,
) -> Tuple[List[str], List[str], Optional[str], Optional[List[Dict[str, str]]], List[Dict[str, str]]]:
    """Парсит HTML, извлекает ссылки на медиа, заголовок и доступные переводы.

    Дополнительно возвращает список словарей с информацией о HLS-ссылках и их качестве.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:
        raise RuntimeError(f"Не удалось разобрать HTML страницы: {exc}") from exc

    page_title: Optional[str] = None
    try:
        title_node = soup.find("title")
        if title_node and getattr(title_node, "text", None):
            candidate = title_node.text.strip()
            if candidate:
                page_title = candidate
    except Exception:
        page_title = None

    hls_urls: list[str] = []
    file_urls: list[str] = []
    hls_entries: list[dict[str, str]] = []
    seen_hls = seen_hls or set()
    seen_files = seen_files or set()

    translations: list[dict[str, str]] = []

    def _infer_quality(tag: Optional[object], raw_value: Optional[str], final_url: str) -> Optional[str]:
        candidates: list[Optional[str]] = []
        if tag is not None:
            try:
                for attr_name in ("data-quality", "data-quality-label", "data-quality-name", "data-hls-quality"):
                    attr_val = tag.get(attr_name)  # type: ignore[attr-defined]
                    if attr_val:
                        candidates.append(attr_val)
                text_content = getattr(tag, "text", None)
            except Exception:
                text_content = None
            if isinstance(text_content, str):
                candidates.append(text_content)
        candidates.extend([raw_value, final_url])
        for candidate_value in candidates:
            guessed = _guess_quality_from_text(candidate_value)
            if guessed:
                return guessed
        return None

    search_root = soup
    if target_hash:
        selector = target_hash.strip()
        if selector:
            node = None
            if selector.startswith("#"):
                node = soup.select_one(selector)
            else:
                node = soup.select_one(f"#{selector}")
            if not node and selector.startswith("#"):
                node = soup.find(id=selector[1:])
            if node:
                search_root = node

    def _add_candidate(raw: Optional[str], tag: Optional[BeautifulSoup]) -> None:
        if not raw:
            return
        candidate_raw = raw
        try:
            parsed_candidate = urlparse(candidate_raw)
            qs = parse_qs(parsed_candidate.query or "")
            file_vals = qs.get("file")
            if file_vals:
                file_url = file_vals[0]
                if isinstance(file_url, str) and file_url:
                    candidate_raw = file_url
        except Exception:
            pass

        candidate = urljoin(page_url, candidate_raw)
        quality = _infer_quality(tag, candidate_raw, candidate)
        quality_str = quality or ""

        if _is_hls_m3u8_url(candidate):
            if candidate not in seen_hls:
                seen_hls.add(candidate)
                hls_urls.append(candidate)
                hls_entries.append({"url": candidate, "quality": quality_str})
        elif _is_direct_video_url(candidate):
            if candidate not in seen_files:
                seen_files.add(candidate)
                file_urls.append(candidate)
        else:
            # Неизвестный тип — игнорируем
            return

    for tag in search_root.find_all(["source", "video", "audio"]):
        _add_candidate(tag.get("src"), tag)

    for tag in search_root.find_all(["a", "link", "iframe"]):
        _add_candidate(tag.get("href") or tag.get("src"), tag)

    for tag in search_root.find_all(True):
        for attr_name, attr_value in tag.attrs.items():
            if not isinstance(attr_value, str):
                continue
            if attr_name.startswith("data-") and any(
                ext in attr_value.lower() for ext in (".m3u8", ".mp4", ".webm", ".mkv", ".mov", ".avi", ".flv")
            ):
                _add_candidate(attr_value, tag)

    url_pattern = re.compile(
        r"""["'](https?://[^\s'\"]+?\.(?:m3u8|mp4|webm|mkv|mov|avi|flv)(?:\?[^\s'\"]*)?)["']""",
        re.IGNORECASE,
    )
    for match in url_pattern.finditer(html):
        candidate_url = match.group(1)
        _add_candidate(candidate_url, None)

    for translation_tag in soup.select("[data-sound-hash][data-sound]"):
        try:
            name = (translation_tag.get_text() or "").strip()
            hash_value = translation_tag.get("data-sound-hash") or ""
            sound_id = translation_tag.get("data-sound") or ""
            if hash_value and name:
                translations.append(
                    {
                        "name": name,
                        "hash": hash_value,
                        "sound_id": sound_id,
                    }
                )
        except Exception:
            continue

    return hls_urls, file_urls, page_title, translations or None, hls_entries


def _guess_quality_from_text(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = str(value)
    match = re.search(r"(\d{3,4})\s*[pP]", text)
    if match:
        return f"{match.group(1)}p"
    match = re.search(r"(\d{3,4})(?=\D*(?:\.mp4|\.webm|\.mkv|\.mov|\.ts))", text)
    if match:
        return f"{match.group(1)}p"
    return None


def find_media_urls(
    url: str,
    cookies_path: str | None = None,
    translation_hash: Optional[str] = None,
) -> Tuple[List[str], List[str], Optional[str], Optional[List[Dict[str, str]]], List[Dict[str, str]]]:
    """Ищет на странице ссылки на HLS-потоки (m3u8) и обычные видеофайлы.

    Args:
        url: URL веб-страницы, которая может содержать ссылки на потоки/файлы.
        cookies_path: Путь к cookies.txt (формат Netscape) для доступа
            к приватному/региональному контенту при необходимости.
        translation_hash: Идентификатор перевода (значение data-sound-hash),
            если необходимо выбрать конкретную дорожку озвучки.

    Returns:
        Кортеж (hls_urls, file_urls, page_title, translations, hls_streams):
            hls_urls: список абсолютных ссылок на HLS-потоки (.m3u8);
            file_urls: список абсолютных ссылок на обычные видеофайлы;
            page_title: заголовок страницы, если удалось определить;
            translations: список словарей доступных переводов (name/hash/sound_id);
            hls_streams: список словарей с подробностями по HLS (url, quality).

    Raises:
        ValueError: если URL некорректен.
        RuntimeError: если не удалось загрузить или разобрать страницу.

    Примечание:
        Если задана переменная окружения ``GVZ_ALLOW_INSECURE_SSL=1`` (или ``true/yes``),
        будет выполнен повторный запрос с отключённой проверкой SSL-сертификата.
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

    # Загружаем HTML-страницу с улучшенной обработкой ошибок и retry.
    headers = _build_headers()
    cookies = _load_cookies(cookies_path)
    allow_insecure_env = os.getenv("GVZ_ALLOW_INSECURE_SSL", "").strip().lower() in {"1", "true", "yes"}
    verify_ssl = not allow_insecure_env

    # Retry логика для обработки временных ошибок соединения
    max_retries = 2
    html = None

    for attempt in range(max_retries + 1):
        try:
            # Создаём сессию для лучшего управления соединениями
            session = requests.Session()
            session.headers.update(headers)
            if cookies:
                session.cookies.update(cookies)
            
            resp = session.get(
                page_url,
                timeout=30,  # Увеличен таймаут
                verify=verify_ssl,
                allow_redirects=True,
            )
            resp.raise_for_status()
            html = resp.text
            break  # Успешно загрузили
        except requests.exceptions.SSLError as ssl_exc:
            if verify_ssl:
                logger.warning("SSL проверка для %s не пройдена, пробуем без проверки сертификата", page_url)
                try:
                    session = requests.Session()
                    session.headers.update(headers)
                    if cookies:
                        session.cookies.update(cookies)
                    resp = session.get(
                        page_url,
                        timeout=30,
                        verify=False,
                        allow_redirects=True,
                    )
                    resp.raise_for_status()
                    html = resp.text
                    break
                except Exception as insecure_exc:
                    clean = _clean_yt_dlp_error_message(str(insecure_exc))
                    raise RuntimeError(
                        "Не удалось загрузить страницу по HTTPS (ошибка сертификата). "
                        "Установите переменную окружения GVZ_ALLOW_INSECURE_SSL=1 для принудительного пропуска проверки. "
                        f"Детали: {clean}"
                    ) from insecure_exc
            else:
                clean = _clean_yt_dlp_error_message(str(ssl_exc))
                raise RuntimeError(
                    "Не удалось загрузить страницу по HTTPS (ошибка сертификата). "
                    "Убедитесь в корректности сертификата или включите разрешение на небезопасные SSL-соединения. "
                    f"Детали: {clean}"
                ) from ssl_exc
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, ConnectionResetError) as conn_exc:
            if attempt < max_retries:
                logger.debug("Повторная попытка загрузки %s (попытка %d/%d)", page_url, attempt + 1, max_retries + 1)
                import time
                time.sleep(1.0 * (attempt + 1))  # Экспоненциальная задержка
                continue
            # Если все попытки исчерпаны
            clean = _clean_yt_dlp_error_message(str(conn_exc))
            raise RuntimeError(
                f"Не удалось загрузить страницу после {max_retries + 1} попыток: соединение разорвано. "
                f"Возможно, сайт блокирует автоматические запросы. "
                f"Попробуйте использовать браузерный парсер (пункт 4 с --use-browser-parser или автоматический fallback). "
                f"Детали: {clean}"
            ) from conn_exc
        except Exception as exc:
            # Для других ошибок не делаем retry, сразу пробрасываем
            clean = _clean_yt_dlp_error_message(str(exc))
            raise RuntimeError(f"Не удалось загрузить страницу для анализа: {clean}") from exc

    if html is None:
        raise RuntimeError("Не удалось загрузить страницу после всех попыток")

    # Разбор HTML через BeautifulSoup.
    return _extract_media_from_html(page_url, html, target_hash=translation_hash)
