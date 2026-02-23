"""
Браузерный парсер для GrabVidZilla (Playwright).

Этот модуль содержит функции для извлечения медиа-ссылок с помощью headless Chromium
через Playwright. Используется для динамических страниц, где ссылки появляются
только после выполнения JavaScript.

Важно:
- модуль относится к слою core и не должен выполнять вывод в CLI/UI;
- ошибки сообщаются через ValueError/RuntimeError с короткими текстами,
  форматирование сообщений остаётся на уровне CLI/UI.
"""

from __future__ import annotations

from http.cookiejar import MozillaCookieJar
from typing import Tuple, List, Optional, Dict
from urllib.parse import urlparse
import logging
import os

# Импортируем общие функции напрямую, чтобы избежать циклических импортов
from core.downloader import _is_hls_m3u8_url
from urllib.parse import urlparse, urljoin, parse_qs
from http.cookiejar import MozillaCookieJar
import re

from bs4 import BeautifulSoup  # type: ignore[import]

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:  # pragma: no cover - Playwright обязателен, но делаем graceful fallback
    sync_playwright = None  # type: ignore[assignment]

    class PlaywrightTimeoutError(Exception):
        """Заглушка, если playwright не установлен."""


logger = logging.getLogger(__name__)


DEFAULT_GOTO_TIMEOUT_MS = int(os.getenv("GVZ_BROWSER_GOTO_TIMEOUT_MS", "15000"))
DEFAULT_AFTER_LOAD_WAIT_MS = int(os.getenv("GVZ_BROWSER_AFTER_LOAD_WAIT_MS", "1000"))


def _normalize_url(url: str) -> str:
    """Простая нормализация URL (обрезка пробелов)."""
    return url.strip()


def _is_direct_video_url(url: str) -> bool:
    """Возвращает True, если URL выглядит как прямая ссылка на видеофайл."""
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
    """Пробует загрузить cookies из файла Netscape-формата."""
    if not cookies_path:
        return None
    try:
        jar = MozillaCookieJar()
        jar.load(cookies_path, ignore_discard=True, ignore_expires=True)
        return jar
    except Exception:
        return None


def _guess_quality_from_text(value: Optional[str]) -> Optional[str]:
    """Угадывает качество видео из текста."""
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


def _extract_media_from_html(
    page_url: str,
    html: str,
    seen_hls: Optional[set[str]] = None,
    seen_files: Optional[set[str]] = None,
    target_hash: Optional[str] = None,
) -> Tuple[List[str], List[str], Optional[str], Optional[List[Dict[str, str]]], List[Dict[str, str]]]:
    """Парсит HTML, извлекает ссылки на медиа, заголовок и доступные переводы."""
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


def _resolve_browser_proxy(proxy: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """Возвращает прокси-конфиг для Playwright, при необходимости из окружения."""
    if proxy:
        return proxy

    server = os.getenv("GVZ_BROWSER_PROXY_SERVER")
    if not server:
        return None

    proxy_conf: Dict[str, str] = {"server": server}
    username = os.getenv("GVZ_BROWSER_PROXY_USERNAME")
    password = os.getenv("GVZ_BROWSER_PROXY_PASSWORD")
    if username:
        proxy_conf["username"] = username
    if password:
        proxy_conf["password"] = password
    return proxy_conf


def _cookies_to_playwright_format(
    cookies: Optional[MozillaCookieJar],
    page_url: str,
) -> List[Dict[str, object]]:
    """Конвертирует cookies Netscape-формата в структуру Playwright."""
    if not cookies:
        return []

    target: list[dict[str, object]] = []
    fallback_domain = urlparse(page_url).hostname or ""

    for cookie in cookies:
        pw_cookie: dict[str, object] = {
            "name": cookie.name,
            "value": cookie.value,
            "path": cookie.path or "/",
            "secure": bool(cookie.secure),
        }

        domain = cookie.domain or fallback_domain
        if domain:
            pw_cookie["domain"] = domain
        else:
            pw_cookie["url"] = page_url

        if cookie.expires is not None:
            pw_cookie["expires"] = cookie.expires

        http_only = cookie.rest.get("HttpOnly") if hasattr(cookie, "rest") else None
        if http_only:
            pw_cookie["httpOnly"] = True

        same_site = cookie.rest.get("SameSite") if hasattr(cookie, "rest") else None
        if same_site:
            pw_cookie["sameSite"] = same_site  # type: ignore[assignment]

        target.append(pw_cookie)

    return target


def fetch_media_urls_with_browser(
    url: str,
    cookies_path: Optional[str] = None,
    proxy: Optional[Dict[str, str]] = None,
    translation_hash: Optional[str] = None,
) -> Tuple[List[str], List[str], Optional[str], Optional[List[Dict[str, str]]], List[Dict[str, str]]]:
    """
    Извлекает медиа-ссылки с помощью headless Chromium и Playwright.

    Args:
        url: URL веб-страницы для анализа.
        cookies_path: Путь к cookies.txt (формат Netscape) для доступа
            к приватному/региональному контенту при необходимости.
        proxy: Прокси-конфигурация для Playwright (опционально).
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
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL должен быть непустой строкой.")

    page_url = _normalize_url(url)
    parsed = urlparse(page_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL должен начинаться с http:// или https://")

    if _is_hls_m3u8_url(page_url):
        quality = _guess_quality_from_text(page_url) or ""
        return [page_url], [], None, None, [{"url": page_url, "quality": quality}]
    if _is_direct_video_url(page_url):
        return [], [page_url], None, None, []

    if sync_playwright is None:
        logger.error("Playwright не установлен. Выполните `pip install playwright` и `python -m playwright install chromium`.")
        return [], [], None, None, []

    resolved_proxy = _resolve_browser_proxy(proxy)
    cookies_jar = _load_cookies(cookies_path)
    playwright_cookies = _cookies_to_playwright_format(cookies_jar, page_url)
    allow_insecure_env = os.getenv("GVZ_ALLOW_INSECURE_SSL", "").strip().lower() in {"1", "true", "yes"}

    hls_urls: list[str] = []
    file_urls: list[str] = []
    hls_entries: list[dict[str, str]] = []
    seen_hls: set[str] = set()
    seen_files: set[str] = set()
    page_title: Optional[str] = None
    translations: Optional[List[Dict[str, str]]] = None

    def _record_url(candidate: str) -> None:
        if _is_hls_m3u8_url(candidate):
            if candidate not in seen_hls:
                seen_hls.add(candidate)
                hls_urls.append(candidate)
        elif _is_direct_video_url(candidate):
            if candidate not in seen_files:
                seen_files.add(candidate)
                file_urls.append(candidate)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                proxy=resolved_proxy,
            )
            context = None
            try:
                context = browser.new_context(
                    user_agent=_build_headers()["User-Agent"],
                    ignore_https_errors=allow_insecure_env,
                )
                if playwright_cookies:
                    context.add_cookies(playwright_cookies)

                page = context.new_page()
                page.on("response", lambda response: _record_url(response.url))

                try:
                    # Используем commit вместо networkidle для ускорения - не ждём полной загрузки всех ресурсов
                    page.goto(
                        page_url,
                        wait_until="commit",  # Быстрее чем domcontentloaded
                        timeout=DEFAULT_GOTO_TIMEOUT_MS,
                    )
                    # Короткая пауза для выполнения критичного JS, но не ждём networkidle
                    page.wait_for_timeout(500)
                except PlaywrightTimeoutError:
                    logger.warning("Не удалось загрузить страницу %s за %d мс", page_url, DEFAULT_GOTO_TIMEOUT_MS)

                if translation_hash:
                    normalized_hash = translation_hash.strip()
                    if normalized_hash:
                        selector = f'[data-sound-hash="{normalized_hash}"]'
                        try:
                            button = page.query_selector(selector)
                            if button:
                                button.click()
                                page.wait_for_timeout(300)  # Уменьшено с 700мс до 300мс
                        except Exception:
                            logger.debug("Не удалось активировать перевод %s", normalized_hash)

                page.wait_for_timeout(DEFAULT_AFTER_LOAD_WAIT_MS)

                try:
                    html = page.content()
                except PlaywrightTimeoutError:
                    logger.warning("Не удалось получить HTML содержимое страницы %s", page_url)
                    html = ""

                if html:
                    extra_hls, extra_files, parsed_title, parsed_translations, extra_entries = _extract_media_from_html(
                        page_url,
                        html,
                        seen_hls=seen_hls,
                        seen_files=seen_files,
                        target_hash=translation_hash,
                    )
                    _recorded_hls = set(hls_urls)
                    _recorded_files = set(file_urls)
                    hls_urls.extend([u for u in extra_hls if u not in _recorded_hls])
                    file_urls.extend([u for u in extra_files if u not in _recorded_files])
                    hls_entries.extend(extra_entries)
                    if not page_title and parsed_title:
                        page_title = parsed_title
                    if parsed_translations:
                        translations = parsed_translations

                if not page_title:
                    try:
                        page_title = page.title()
                    except PlaywrightTimeoutError:
                        logger.debug("Не удалось получить title страницы %s", page_url)
                    except Exception:
                        page_title = None
            finally:
                if context:
                    context.close()
                browser.close()
    except Exception:
        logger.exception("Не удалось собрать медиа-ссылки через браузер для %s", page_url)
        return [], [], None, None, []

    return hls_urls, file_urls, page_title, translations, hls_entries

