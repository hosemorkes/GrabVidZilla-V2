"""
Адаптер парсера для сайта fanserials (1fanserials.ru и другие домены).

Сайт использует PlayerJS для воспроизведения видео через iframe.
Адаптер извлекает доступные переводы и ссылки на видео из плеера.
"""

from __future__ import annotations

import logging
import re
from typing import Tuple, List, Optional, Dict
from urllib.parse import urljoin, urlparse, parse_qs, urlunparse
import os

from bs4 import BeautifulSoup  # type: ignore[import]
import requests

from core.site_parsers.base import SiteParserAdapter
from core.parser import _build_headers, _load_cookies, _is_hls_m3u8_url, _is_direct_video_url, _guess_quality_from_text
from core.browser_parser import fetch_media_urls_with_browser

logger = logging.getLogger(__name__)


class FanserialsAdapter(SiteParserAdapter):
    """
    Адаптер для парсинга сайта fanserials.
    
    Обрабатывает страницы с PlayerJS плеером, извлекает доступные переводы
    и ссылки на видео через браузерный парсер.
    """

    def can_handle(self, url: str) -> bool:
        """
        Проверяет, содержит ли URL "fanserials" в домене.
        
        Это позволяет работать с разными доменами сайта (1fanserials.ru и др.).
        """
        if not isinstance(url, str) or not url.strip():
            return False
        try:
            parsed = urlparse(url.lower())
            hostname = parsed.hostname or ""
            return "fanserials" in hostname
        except Exception:
            return False

    def parse(
        self,
        url: str,
        cookies_path: Optional[str] = None,
        translation_hash: Optional[str] = None,
        proxy: Optional[Dict[str, str]] = None,
    ) -> Tuple[List[str], List[str], Optional[str], Optional[List[Dict[str, str]]], List[Dict[str, str]]]:
        """
        Парсит страницу fanserials и извлекает медиа-ссылки.
        
        Процесс:
        1. Загружает основную страницу и извлекает доступные переводы
        2. Если указан translation_hash, обновляет URL с хешем
        3. Загружает страницу с выбранным переводом
        4. Находит iframe с плеером
        5. Использует браузерный парсер для извлечения ссылок из iframe
        """
        if not isinstance(url, str) or not url.strip():
            raise ValueError("URL должен быть непустой строкой")

        # Нормализуем URL
        page_url = url.strip()
        parsed = urlparse(page_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("URL должен начинаться с http:// или https://")

        # Если указан translation_hash, добавляем его в URL как якорь
        if translation_hash:
            # Убираем старый якорь и добавляем новый
            parsed = list(parsed)
            parsed[5] = translation_hash  # fragment (якорь)
            page_url = urlunparse(parsed)

        # Загружаем основную страницу для извлечения переводов
        headers = _build_headers()
        cookies = _load_cookies(cookies_path)
        allow_insecure_env = os.getenv("GVZ_ALLOW_INSECURE_SSL", "").strip().lower() in {"1", "true", "yes"}
        verify_ssl = not allow_insecure_env

        session = requests.Session()
        session.headers.update(headers)
        if cookies:
            session.cookies.update(cookies)

        # Retry логика с обработкой SSL ошибок
        # Для проблемных сайтов автоматически пробуем без проверки SSL при ошибке сертификата
        max_retries = 2
        html = None
        ssl_error_occurred = False
        
        for attempt in range(max_retries + 1):
            try:
                resp = session.get(page_url, timeout=30, verify=verify_ssl, allow_redirects=True)
                resp.raise_for_status()
                html = resp.text
                break  # Успешно загрузили
            except requests.exceptions.SSLError as ssl_exc:
                ssl_error_occurred = True
                if verify_ssl:
                    # Автоматически пробуем без проверки SSL при ошибке сертификата
                    logger.warning("SSL проверка не пройдена для %s, пробуем без проверки сертификата", page_url)
                    try:
                        resp = session.get(page_url, timeout=30, verify=False, allow_redirects=True)
                        resp.raise_for_status()
                        html = resp.text
                        verify_ssl = False  # Обновляем для последующих запросов
                        break
                    except Exception as insecure_exc:
                        raise RuntimeError(
                            f"Не удалось загрузить страницу по HTTPS (ошибка сертификата). "
                            f"Детали: {insecure_exc}"
                        ) from insecure_exc
                else:
                    raise RuntimeError(f"Не удалось загрузить страницу: ошибка SSL. Детали: {ssl_exc}") from ssl_exc
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, ConnectionResetError) as conn_exc:
                if attempt < max_retries:
                    logger.debug("Повторная попытка загрузки %s (попытка %d/%d)", page_url, attempt + 1, max_retries + 1)
                    import time
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise RuntimeError(
                    f"Не удалось загрузить страницу после {max_retries + 1} попыток: соединение разорвано. "
                    f"Детали: {conn_exc}"
                ) from conn_exc
            except Exception as exc:
                logger.debug("Ошибка загрузки страницы fanserials: %s", exc)
                raise RuntimeError(f"Не удалось загрузить страницу: {exc}") from exc
        
        if html is None:
            raise RuntimeError("Не удалось загрузить страницу после всех попыток")

        # Парсим HTML для извлечения переводов и информации о странице
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:
            raise RuntimeError(f"Не удалось разобрать HTML страницы: {exc}") from exc

        # Извлекаем заголовок страницы
        page_title: Optional[str] = None
        try:
            title_node = soup.find("title")
            if title_node and getattr(title_node, "text", None):
                candidate = title_node.text.strip()
                if candidate:
                    page_title = candidate
        except Exception:
            pass

        # Извлекаем доступные переводы из элементов с data-sound-hash
        translations: List[Dict[str, str]] = []
        for translation_tag in soup.select(".sound-item[data-sound-hash], [data-sound-hash]"):
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

        # Если указан translation_hash, но его нет в списке переводов - всё равно продолжаем
        # (возможно, он был передан извне)

        # Ищем iframe с плеером
        iframe_src: Optional[str] = None
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src") or iframe.get("data-src")
            if src and ("player" in src.lower() or "anime.php" in src.lower()):
                iframe_src = src
                break

        # Если iframe найден, нужно разрешить его URL
        if iframe_src:
            # Если iframe src относительный, делаем его абсолютным
            if not iframe_src.startswith(("http://", "https://")):
                iframe_src = urljoin(page_url, iframe_src)
            
            # Если iframe содержит вложенный URL (как в примере: /player/anime.php?url=/player/anime.php?url=...)
            # извлекаем внутренний URL
            parsed_iframe = urlparse(iframe_src)
            qs = parse_qs(parsed_iframe.query)
            nested_urls = qs.get("url", [])
            if nested_urls:
                # Берём последний вложенный URL
                inner_url = nested_urls[-1]
                if isinstance(inner_url, str):
                    # Если внутренний URL тоже относительный
                    if not inner_url.startswith(("http://", "https://")):
                        # Пробуем разрешить относительно домена iframe
                        iframe_base = f"{parsed_iframe.scheme}://{parsed_iframe.netloc}"
                        inner_url = urljoin(iframe_base, inner_url)
                    iframe_src = inner_url

        hls_urls: List[str] = []
        file_urls: List[str] = []
        hls_streams: List[Dict[str, str]] = []

        # Используем браузерный парсер для извлечения ссылок из iframe/страницы
        # Браузерный парсер лучше справляется с динамически загружаемым контентом PlayerJS
        # Особенно важно для сайтов, где ссылки генерируются через JavaScript
        try:
            browser_hls, browser_files, browser_title, browser_translations, browser_streams = fetch_media_urls_with_browser(
                page_url,
                cookies_path=cookies_path,
                proxy=proxy,
                translation_hash=translation_hash,
            )
            hls_urls.extend(browser_hls)
            file_urls.extend(browser_files)
            hls_streams.extend(browser_streams)
            
            # Если браузерный парсер нашёл заголовок, используем его
            if browser_title and not page_title:
                page_title = browser_title
            
            # Если браузерный парсер нашёл переводы, объединяем с найденными статически
            if browser_translations:
                # Объединяем переводы, избегая дубликатов
                existing_hashes = {t.get("hash") for t in translations}
                for bt in browser_translations:
                    if bt.get("hash") not in existing_hashes:
                        translations.append(bt)
        except Exception as exc:
            logger.warning("Браузерный парсер не смог извлечь ссылки: %s", exc)
            # Продолжаем работу, возможно статический парсинг найдёт что-то

        # Дополнительно пробуем извлечь ссылки статически из HTML
        # Ищем ссылки на kodik.info и другие источники
        kodik_pattern = re.compile(r'kodik\.info[^"\'>\s]+', re.IGNORECASE)
        for match in kodik_pattern.finditer(html):
            found_url = match.group(0)
            if not found_url.startswith(("http://", "https://")):
                found_url = f"https://{found_url}"
            if _is_hls_m3u8_url(found_url):
                if found_url not in hls_urls:
                    hls_urls.append(found_url)
                    quality = _guess_quality_from_text(found_url) or ""
                    hls_streams.append({"url": found_url, "quality": quality})
            elif _is_direct_video_url(found_url):
                if found_url not in file_urls:
                    file_urls.append(found_url)

        # Если iframe найден, пробуем загрузить его содержимое напрямую
        # Это может помочь, если браузерный парсер не смог извлечь ссылки
        if iframe_src:
            try:
                iframe_resp = session.get(iframe_src, timeout=30, verify=verify_ssl if not allow_insecure_env else False, allow_redirects=True)
                iframe_resp.raise_for_status()
                iframe_html = iframe_resp.text
                
                # Ищем ссылки в содержимом iframe
                iframe_soup = BeautifulSoup(iframe_html, "html.parser")
                
                # Ищем ссылки на видео в iframe
                for tag in iframe_soup.find_all(["source", "video", "audio"]):
                    src = tag.get("src")
                    if src:
                        full_url = urljoin(iframe_src, src)
                        if _is_hls_m3u8_url(full_url):
                            if full_url not in hls_urls:
                                hls_urls.append(full_url)
                                quality = _guess_quality_from_text(full_url) or ""
                                hls_streams.append({"url": full_url, "quality": quality})
                        elif _is_direct_video_url(full_url):
                            if full_url not in file_urls:
                                file_urls.append(full_url)
                
                # Ищем ссылки в JavaScript коде iframe
                # PlayerJS может хранить ссылки в различных форматах
                for script in iframe_soup.find_all("script"):
                    script_text = script.string or ""
                    if not script_text:
                        continue
                    
                    # Ищем паттерны типа "file": "http://..." или "url": "http://..."
                    # Также ищем ссылки в массивах и объектах PlayerJS
                    url_patterns = [
                        r'["\']file["\']\s*:\s*["\']([^"\']+)["\']',
                        r'["\']url["\']\s*:\s*["\']([^"\']+)["\']',
                        r'["\']src["\']\s*:\s*["\']([^"\']+)["\']',
                        r'["\']source["\']\s*:\s*["\']([^"\']+)["\']',
                        r'file:\s*["\']([^"\']+)["\']',
                        r'url:\s*["\']([^"\']+)["\']',
                        # Ищем прямые ссылки на m3u8 и видеофайлы
                        r'(https?://[^\s"\'>]+\.(?:m3u8|mp4|webm|mkv))',
                    ]
                    for pattern in url_patterns:
                        for match in re.finditer(pattern, script_text, re.IGNORECASE):
                            found_url = match.group(1)
                            if not found_url or len(found_url) < 10:
                                continue
                            full_url = urljoin(iframe_src, found_url)
                            if _is_hls_m3u8_url(full_url):
                                if full_url not in hls_urls:
                                    hls_urls.append(full_url)
                                    quality = _guess_quality_from_text(full_url) or ""
                                    hls_streams.append({"url": full_url, "quality": quality})
                            elif _is_direct_video_url(full_url):
                                if full_url not in file_urls:
                                    file_urls.append(full_url)
            except Exception as exc:
                logger.debug("Не удалось загрузить iframe напрямую: %s", exc)
                # Продолжаем работу

        return (
            hls_urls,
            file_urls,
            page_title,
            translations if translations else None,
            hls_streams,
        )

