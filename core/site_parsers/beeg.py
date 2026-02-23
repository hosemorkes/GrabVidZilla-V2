"""
Адаптер парсера для сайта beeg.com.

Сайт использует blob URL для видео, поэтому требуется браузерный парсер
для извлечения реальных ссылок на видеофайлы.
"""

from __future__ import annotations

import logging
import re
from typing import Tuple, List, Optional, Dict
from urllib.parse import urljoin, urlparse
import os

from bs4 import BeautifulSoup  # type: ignore[import]
import requests

from core.site_parsers.base import SiteParserAdapter
from core.parser import _build_headers, _load_cookies, _is_hls_m3u8_url, _is_direct_video_url, _guess_quality_from_text
from core.browser_parser import fetch_media_urls_with_browser

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:
    sync_playwright = None
    PlaywrightTimeoutError = Exception

logger = logging.getLogger(__name__)


class BeegAdapter(SiteParserAdapter):
    """
    Адаптер для парсинга сайта beeg.com.
    
    Обрабатывает страницы с blob URL видео, извлекает доступные качества
    и ссылки на видео через браузерный парсер.
    """

    def can_handle(self, url: str) -> bool:
        """
        Проверяет, содержит ли URL "beeg" в домене.
        
        Это позволяет работать с разными доменами сайта.
        """
        if not isinstance(url, str) or not url.strip():
            return False
        try:
            parsed = urlparse(url.lower())
            hostname = parsed.hostname or ""
            return "beeg" in hostname
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
        Парсит страницу beeg.com и извлекает медиа-ссылки.
        
        Процесс:
        1. Загружает основную страницу и извлекает доступные качества
        2. Если указан translation_hash (используется как качество), фильтрует по нему
        3. Использует браузерный парсер для извлечения реальных ссылок из blob URL
        """
        if not isinstance(url, str) or not url.strip():
            raise ValueError("URL должен быть непустой строкой")

        # Нормализуем URL
        page_url = url.strip()
        parsed = urlparse(page_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("URL должен начинаться с http:// или https://")

        # Загружаем основную страницу для извлечения качеств
        headers = _build_headers()
        cookies = _load_cookies(cookies_path)
        allow_insecure_env = os.getenv("GVZ_ALLOW_INSECURE_SSL", "").strip().lower() in {"1", "true", "yes"}
        verify_ssl = not allow_insecure_env

        session = requests.Session()
        session.headers.update(headers)
        if cookies:
            session.cookies.update(cookies)

        # Retry логика с обработкой SSL ошибок
        max_retries = 2
        html = None
        
        for attempt in range(max_retries + 1):
            try:
                resp = session.get(page_url, timeout=30, verify=verify_ssl, allow_redirects=True)
                resp.raise_for_status()
                html = resp.text
                break  # Успешно загрузили
            except requests.exceptions.SSLError as ssl_exc:
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
                logger.debug("Ошибка загрузки страницы beeg: %s", exc)
                raise RuntimeError(f"Не удалось загрузить страницу: {exc}") from exc
        
        if html is None:
            raise RuntimeError("Не удалось загрузить страницу после всех попыток")

        # Парсим HTML для извлечения качеств и информации о странице
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

        # Извлекаем доступные качества из элементов списка
        # Ищем элементы с классами, содержащими качество (1080p, 720p и т.д.)
        qualities: List[Dict[str, str]] = []
        
        # Паттерн для поиска качества (1080p, 720p, Auto и т.д.)
        quality_pattern = re.compile(r'\b(\d+p|auto(?:\s*\(\d+p\))?)\b', re.IGNORECASE)
        
        # Сначала пробуем извлечь качества через браузерный парсер (если доступен)
        # так как качества могут загружаться динамически через JavaScript
        if sync_playwright is not None:
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True)
                    context = browser.new_context(
                        user_agent=_build_headers()["User-Agent"],
                        ignore_https_errors=allow_insecure_env,
                    )
                    if cookies:
                        from core.browser_parser import _cookies_to_playwright_format
                        pw_cookies = _cookies_to_playwright_format(cookies, page_url)
                        if pw_cookies:
                            context.add_cookies(pw_cookies)
                    
                    page = context.new_page()
                    try:
                        page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                        # Увеличиваем время ожидания для загрузки динамического контента
                        page.wait_for_timeout(5000)  # 5 секунд для загрузки качеств
                        
                        # Извлекаем качества через JavaScript
                        browser_qualities = page.evaluate("""
                            () => {
                                const qualities = [];
                                const qualitySet = new Set();
                                
                                // Функция нормализации качества
                                function normalizeQuality(q) {
                                    q = q.trim();
                                    if (q.toLowerCase().includes('auto')) {
                                        return 'Auto';
                                    }
                                    q = q.toLowerCase();
                                    // Убираем скобки и текст внутри (например, "Auto (360p)" -> "Auto")
                                    q = q.replace(/\\([^)]*\\)/g, '').trim();
                                    if (q.toLowerCase() === 'auto') {
                                        return 'Auto';
                                    }
                                    // Извлекаем число и добавляем 'p'
                                    const numMatch = q.match(/(\\d+)/);
                                    if (numMatch) {
                                        return numMatch[1] + 'p';
                                    }
                                    if (!q.endsWith('p') && q.match(/\\d/)) {
                                        return q + 'p';
                                    }
                                    return q;
                                }
                                
                                // Ищем в button элементах
                                const buttons = document.querySelectorAll('button');
                                buttons.forEach(button => {
                                    const buttonText = button.innerText.trim();
                                    // Ищем паттерн качества (1080p, 720p, Auto и т.д.)
                                    const match = buttonText.match(/\\b(\\d+p|auto(?:\\s*\\(\\d+p\\))?)\\b/i);
                                    if (match) {
                                        const quality = normalizeQuality(match[1]);
                                        if (quality && !qualitySet.has(quality)) {
                                            qualitySet.add(quality);
                                            qualities.push(quality);
                                        }
                                    }
                                    
                                    // Также проверяем дочерние div элементы
                                    const divs = button.querySelectorAll('div');
                                    divs.forEach(div => {
                                        const divText = div.innerText.trim();
                                        if (divText && divText.length <= 30) {
                                            const divMatch = divText.match(/\\b(\\d+p|auto(?:\\s*\\(\\d+p\\))?)\\b/i);
                                            if (divMatch) {
                                                const quality = normalizeQuality(divMatch[1]);
                                                if (quality && !qualitySet.has(quality)) {
                                                    qualitySet.add(quality);
                                                    qualities.push(quality);
                                                }
                                            }
                                        }
                                    });
                                });
                                
                                // Также ищем в div элементах с классами tw-flex или tw-truncate
                                const divs = document.querySelectorAll('div');
                                divs.forEach(div => {
                                    const classes = div.className || '';
                                    if (classes.includes('tw-flex') || classes.includes('tw-truncate')) {
                                        const divText = div.innerText.trim();
                                        if (divText && divText.length <= 30) {
                                            const match = divText.match(/\\b(\\d+p|auto(?:\\s*\\(\\d+p\\))?)\\b/i);
                                            if (match) {
                                                const quality = normalizeQuality(match[1]);
                                                if (quality && !qualitySet.has(quality)) {
                                                    qualitySet.add(quality);
                                                    qualities.push(quality);
                                                }
                                            }
                                        }
                                    }
                                });
                                
                                // Также ищем в span элементах
                                const spans = document.querySelectorAll('span');
                                spans.forEach(span => {
                                    const spanText = span.innerText.trim();
                                    if (spanText && spanText.length <= 20) {
                                        const match = spanText.match(/\\b(\\d+p|auto(?:\\s*\\(\\d+p\\))?)\\b/i);
                                        if (match) {
                                            const quality = normalizeQuality(match[1]);
                                            if (quality && !qualitySet.has(quality)) {
                                                qualitySet.add(quality);
                                                qualities.push(quality);
                                            }
                                        }
                                    }
                                });
                                
                                return qualities;
                            }
                        """)
                        
                        # Добавляем найденные качества
                        logger.debug("Найдено качеств через браузер: %s", browser_qualities)
                        for q in browser_qualities:
                            if q and isinstance(q, str):
                                q_normalized = q.lower()
                                if q_normalized == "auto":
                                    q_normalized = "Auto"
                                if not any(existing.get("name", "").lower() == q_normalized for existing in qualities):
                                    qualities.append({
                                        "name": q_normalized,
                                        "hash": q_normalized,
                                        "quality": q_normalized,
                                    })
                        
                        # Если качества не найдены, пробуем более агрессивный поиск
                        if not browser_qualities or len(browser_qualities) == 0:
                            logger.debug("Качества не найдены, пробуем альтернативный метод")
                            # Пробуем найти все текстовые элементы, содержащие паттерн качества
                            all_text_qualities = page.evaluate("""
                                () => {
                                    const qualities = [];
                                    const qualitySet = new Set();
                                    const qualityRegex = /\\b(\\d+p|auto(?:\\s*\\(\\d+p\\))?)\\b/gi;
                                    
                                    // Получаем весь текст страницы
                                    const walker = document.createTreeWalker(
                                        document.body,
                                        NodeFilter.SHOW_TEXT,
                                        null,
                                        false
                                    );
                                    
                                    let node;
                                    while (node = walker.nextNode()) {
                                        const text = node.textContent.trim();
                                        if (text && text.length <= 30) {
                                            const matches = text.matchAll(qualityRegex);
                                            for (const match of matches) {
                                                let quality = match[1].trim();
                                                if (quality.toLowerCase().includes('auto')) {
                                                    quality = 'Auto';
                                                } else {
                                                    quality = quality.toLowerCase();
                                                    if (!quality.endsWith('p')) {
                                                        const numMatch = quality.match(/(\\d+)/);
                                                        if (numMatch) {
                                                            quality = numMatch[1] + 'p';
                                                        } else {
                                                            quality = quality + 'p';
                                                        }
                                                    }
                                                }
                                                if (quality && !qualitySet.has(quality)) {
                                                    qualitySet.add(quality);
                                                    qualities.push(quality);
                                                }
                                            }
                                        }
                                    }
                                    
                                    return qualities;
                                }
                            """)
                            logger.debug("Найдено качеств альтернативным методом: %s", all_text_qualities)
                            for q in all_text_qualities:
                                if q and isinstance(q, str):
                                    q_normalized = q.lower()
                                    if q_normalized == "auto":
                                        q_normalized = "Auto"
                                    if not any(existing.get("name", "").lower() == q_normalized for existing in qualities):
                                        qualities.append({
                                            "name": q_normalized,
                                            "hash": q_normalized,
                                            "quality": q_normalized,
                                        })
                    except Exception as exc:
                        logger.debug("Не удалось извлечь качества через браузер: %s", exc)
                        import traceback
                        logger.debug(traceback.format_exc())
                    finally:
                        context.close()
                        browser.close()
            except Exception as exc:
                logger.debug("Не удалось использовать Playwright для извлечения качеств: %s", exc)
        
        # Ищем в button элементах - на beeg.com качества часто в кнопках
        # Пример: <button>...<div>1080p</div></button>
        for button in soup.find_all("button"):
            # Проверяем текст кнопки и всех дочерних элементов
            button_text = (button.get_text() or "").strip()
            if button_text:
                # Ищем паттерн качества в тексте кнопки
                match = quality_pattern.search(button_text)
                if match:
                    quality_value = match.group(1).strip()
                    # Обрабатываем "Auto (360p)" -> "Auto"
                    if "auto" in quality_value.lower():
                        quality_value = "Auto"
                    else:
                        # Приводим к формату "1080p"
                        quality_value = quality_value.lower()
                        if not quality_value.endswith("p"):
                            quality_value = f"{quality_value}p"
                    
                    if not any(q.get("name", "").lower() == quality_value.lower() for q in qualities):
                        qualities.append({
                            "name": quality_value,
                            "hash": quality_value,
                            "quality": quality_value,
                        })
            
            # Также проверяем дочерние div элементы
            for div in button.find_all("div", recursive=True):
                div_text = (div.get_text() or "").strip()
                if div_text and len(div_text) <= 20:  # Короткий текст, вероятно качество
                    match = quality_pattern.search(div_text)
                    if match:
                        quality_value = match.group(1).strip()
                        if "auto" in quality_value.lower():
                            quality_value = "Auto"
                        else:
                            quality_value = quality_value.lower()
                            if not quality_value.endswith("p"):
                                quality_value = f"{quality_value}p"
                        
                        if not any(q.get("name", "").lower() == quality_value.lower() for q in qualities):
                            qualities.append({
                                "name": quality_value,
                                "hash": quality_value,
                                "quality": quality_value,
                            })
        
        # Ищем в ul списках с классами, содержащими tw-flex (Tailwind CSS классы)
        # Пример: <ul class="tw-flex tw-list-none tw-flex-col ...">
        for ul in soup.find_all("ul", class_=lambda x: x and ("tw-flex" in str(x) or "list" in str(x).lower())):
            for li in ul.find_all("li"):
                text = (li.get_text() or "").strip()
                if not text:
                    continue
                # Ищем паттерн качества (1080p, 720p, Auto и т.д.)
                match = quality_pattern.search(text)
                if match:
                    quality_value = match.group(1).strip()
                    # Нормализуем качество
                    if "auto" in quality_value.lower():
                        quality_value = "Auto"
                    else:
                        # Приводим к формату "1080p" (уже должно быть с "p")
                        quality_value = quality_value.lower()
                        if not quality_value.endswith("p"):
                            quality_value = f"{quality_value}p"
                    
                    # Проверяем, нет ли уже такого качества
                    if not any(q.get("name", "").lower() == quality_value.lower() for q in qualities):
                        qualities.append({
                            "name": quality_value,
                            "hash": quality_value,  # Используем качество как hash для совместимости
                            "quality": quality_value,
                        })
        
        # Также ищем в div элементах с текстом качества
        for div in soup.find_all("div", class_=lambda x: x and ("tw-flex" in str(x) or "tw-truncate" in str(x))):
            text = (div.get_text() or "").strip()
            if text and len(text) <= 20:  # Короткий текст, вероятно качество
                match = quality_pattern.search(text)
                if match:
                    quality_value = match.group(1).strip()
                    if "auto" in quality_value.lower():
                        quality_value = "Auto"
                    else:
                        quality_value = quality_value.lower()
                        if not quality_value.endswith("p"):
                            quality_value = f"{quality_value}p"
                    
                    if not any(q.get("name", "").lower() == quality_value.lower() for q in qualities):
                        qualities.append({
                            "name": quality_value,
                            "hash": quality_value,
                            "quality": quality_value,
                        })
        
        # Также ищем в data-атрибутах и других местах
        for element in soup.find_all(True, attrs={"data-quality": True}):
            quality_value = element.get("data-quality", "").strip()
            if quality_value:
                if quality_value.lower() == "auto":
                    quality_value = "Auto"
                else:
                    quality_value = quality_value.lower()
                    if not quality_value.endswith("p"):
                        quality_value = f"{quality_value}p"
                
                if not any(q.get("name", "").lower() == quality_value.lower() for q in qualities):
                    qualities.append({
                        "name": quality_value,
                        "hash": quality_value,
                        "quality": quality_value,
                    })
        
        # Сортируем качества по убыванию (1080p, 720p, 480p, 360p, 240p, Auto)
        def _sort_quality_key(q: Dict[str, str]) -> int:
            name = q.get("name", "").lower()
            if name == "auto":
                return 0  # Auto в конец
            # Извлекаем число из "1080p" -> 1080
            match = re.search(r'(\d+)', name)
            if match:
                return -int(match.group(1))  # Отрицательное для сортировки по убыванию
            return 999  # Неизвестные в конец
        
        qualities.sort(key=_sort_quality_key)
        
        # Если найдено только "Auto", но нет других качеств, пробуем добавить стандартные качества
        # на основе найденных ссылок или добавляем их как возможные варианты
        if len(qualities) == 1 and qualities[0].get("name", "").lower() == "auto":
            logger.debug("Найдено только Auto, добавляем стандартные качества")
            # Добавляем стандартные качества, которые обычно есть на beeg.com
            standard_qualities = ["1080p", "720p", "480p", "360p", "240p"]
            for sq in standard_qualities:
                if not any(q.get("name", "").lower() == sq.lower() for q in qualities):
                    qualities.insert(0, {
                        "name": sq,
                        "hash": sq,
                        "quality": sq,
                    })

        hls_urls: List[str] = []
        file_urls: List[str] = []
        hls_streams: List[Dict[str, str]] = []

        # Используем браузерный парсер для извлечения ссылок из blob URL
        # Браузерный парсер необходим, так как видео загружается через blob URL
        # и реальная ссылка доступна только после выполнения JavaScript
        try:
            # Если указано качество через translation_hash, используем его
            quality_filter = translation_hash if translation_hash else None
            
            # Для beeg.com используем специальную логику через Playwright напрямую
            # чтобы извлечь ссылки из video элементов
            if sync_playwright is not None:
                try:
                    with sync_playwright() as pw:
                        browser = pw.chromium.launch(headless=True)
                        context = browser.new_context(
                            user_agent=_build_headers()["User-Agent"],
                            ignore_https_errors=allow_insecure_env,
                        )
                        if cookies:
                            # Конвертируем cookies в формат Playwright
                            from core.browser_parser import _cookies_to_playwright_format
                            pw_cookies = _cookies_to_playwright_format(cookies, page_url)
                            if pw_cookies:
                                context.add_cookies(pw_cookies)
                        
                        page = context.new_page()
                        
                        # Отслеживаем сетевые запросы для видео
                        video_urls_found = []
                        quality_filter_num = None
                        if quality_filter and quality_filter.lower() != "auto":
                            try:
                                quality_filter_num = quality_filter.lower().replace("p", "").strip()
                            except Exception:
                                pass
                        
                        def handle_response(response):
                            url = response.url
                            # Проверяем, является ли это видеофайлом
                            if _is_hls_m3u8_url(url) or _is_direct_video_url(url):
                                # Если указано качество, проверяем, что URL содержит нужное качество
                                if quality_filter_num:
                                    url_lower = url.lower()
                                    # Проверяем наличие качества в URL
                                    if (f"/{quality_filter_num}p/" in url_lower or 
                                        f"/{quality_filter_num}/" in url_lower or
                                        f"_{quality_filter_num}p" in url_lower or
                                        f"quality={quality_filter_num}" in url_lower):
                                        if url not in video_urls_found:
                                            video_urls_found.append(url)
                                            logger.debug("Найдена ссылка с качеством %s: %s", quality_filter, url)
                                else:
                                    # Если качество не указано, добавляем все ссылки
                                    if url not in video_urls_found:
                                        video_urls_found.append(url)
                        
                        page.on("response", handle_response)
                        
                        try:
                            page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                            # Ждём загрузки видео - для beeg.com нужно больше времени
                            page.wait_for_timeout(3000)  # 3 секунды для загрузки видео
                            
                            # Если указано качество, пытаемся кликнуть на соответствующую кнопку
                            if quality_filter:
                                quality_to_find = quality_filter.lower()
                                # Нормализуем качество для поиска
                                if quality_to_find == "auto":
                                    quality_to_find = "auto"
                                else:
                                    # Убираем "p" для более гибкого поиска
                                    quality_to_find_clean = quality_to_find.replace("p", "").strip()
                                
                                # Ищем кнопку с нужным качеством через JavaScript
                                try:
                                    button_found = page.evaluate(f"""
                                        () => {{
                                            const qualityToFind = '{quality_to_find}';
                                            const qualityClean = '{quality_to_find_clean if quality_filter.lower() != "auto" else ""}';
                                            
                                            // Ищем все кнопки
                                            const buttons = document.querySelectorAll('button');
                                            for (const button of buttons) {{
                                                const buttonText = button.innerText.trim().toLowerCase();
                                                
                                                // Проверяем точное совпадение
                                                if (buttonText.includes(qualityToFind)) {{
                                                    // Проверяем, что это действительно качество (содержит число или auto)
                                                    if (buttonText.match(/\\b(\\d+p|auto)\\b/i)) {{
                                                        button.click();
                                                        return true;
                                                    }}
                                                }}
                                                
                                                // Также проверяем дочерние элементы
                                                const divs = button.querySelectorAll('div');
                                                for (const div of divs) {{
                                                    const divText = div.innerText.trim().toLowerCase();
                                                    if (divText.includes(qualityToFind) || (qualityClean && divText.includes(qualityClean))) {{
                                                        if (divText.match(/\\b(\\d+p|auto)\\b/i)) {{
                                                            button.click();
                                                            return true;
                                                        }}
                                                    }}
                                                }}
                                            }}
                                            return false;
                                        }}
                                    """)
                                    
                                    if button_found:
                                        # Очищаем старые ссылки перед сменой качества
                                        video_urls_found.clear()
                                        
                                        # Ждём загрузки нового качества и обновления видео
                                        page.wait_for_timeout(5000)  # Увеличено до 5 секунд для загрузки нового качества
                                        
                                        # Ждём, пока видео загрузится (проверяем, что src изменился)
                                        try:
                                            page.wait_for_function(
                                                "document.querySelector('video') && document.querySelector('video').src && !document.querySelector('video').src.startsWith('blob:')",
                                                timeout=10000
                                            )
                                        except Exception:
                                            logger.debug("Таймаут ожидания загрузки видео после смены качества")
                                        
                                        # Дополнительно ждём сетевые запросы
                                        page.wait_for_timeout(2000)
                                        
                                        logger.debug("Активировано качество %s, найдено ссылок: %d", quality_filter, len(video_urls_found))
                                    else:
                                        logger.debug("Кнопка с качеством %s не найдена", quality_filter)
                                except Exception as exc:
                                    logger.debug("Не удалось активировать качество %s: %s", quality_filter, exc)
                            
                            # Пробуем извлечь ссылки из video элементов через JavaScript
                            # Также проверяем, что ссылка соответствует выбранному качеству
                            video_srcs = page.evaluate(f"""
                                () => {{
                                    const videos = document.querySelectorAll('video');
                                    const sources = [];
                                    const qualityFilter = '{quality_filter.lower() if quality_filter else ""}';
                                    
                                    videos.forEach(video => {{
                                        // Проверяем src
                                        if (video.src && !video.src.startsWith('blob:')) {{
                                            sources.push(video.src);
                                        }}
                                        // Проверяем source элементы внутри video
                                        const sourceElements = video.querySelectorAll('source');
                                        sourceElements.forEach(source => {{
                                            if (source.src && !source.src.startsWith('blob:')) {{
                                                sources.push(source.src);
                                            }}
                                        }});
                                        // Пробуем получить текущий источник через currentSrc
                                        if (video.currentSrc && !video.currentSrc.startsWith('blob:')) {{
                                            sources.push(video.currentSrc);
                                        }}
                                    }});
                                    
                                    // Если указано качество, фильтруем ссылки по качеству
                                    if (qualityFilter && qualityFilter !== 'auto') {{
                                        const qualityNum = qualityFilter.replace('p', '');
                                        const filtered = sources.filter(src => {{
                                            // Ищем качество в URL (например, /1080p/ или /1080/)
                                            const urlLower = src.toLowerCase();
                                            return urlLower.includes('/' + qualityNum + 'p/') || 
                                                   urlLower.includes('/' + qualityNum + '/') ||
                                                   urlLower.includes('_' + qualityNum + 'p') ||
                                                   urlLower.includes('quality=' + qualityNum);
                                        }});
                                        // Если найдены ссылки с нужным качеством, возвращаем их
                                        if (filtered.length > 0) {{
                                            return filtered;
                                        }}
                                    }}
                                    
                                    return sources;
                                }}
                            """)
                            
                            # Добавляем найденные ссылки из video элементов
                            # Приоритет отдаём ссылкам с нужным качеством
                            for src in video_srcs:
                                if src and isinstance(src, str):
                                    # Проверяем качество в URL, если указано
                                    if quality_filter_num:
                                        src_lower = src.lower()
                                        if (f"/{quality_filter_num}p/" not in src_lower and 
                                            f"/{quality_filter_num}/" not in src_lower and
                                            f"_{quality_filter_num}p" not in src_lower and
                                            f"quality={quality_filter_num}" not in src_lower):
                                            # Пропускаем ссылки без нужного качества
                                            continue
                                    
                                    if _is_hls_m3u8_url(src):
                                        if src not in hls_urls:
                                            hls_urls.append(src)
                                            quality = quality_filter if quality_filter else (_guess_quality_from_text(src) or "")
                                            hls_streams.append({"url": src, "quality": quality})
                                    elif _is_direct_video_url(src):
                                        if src not in file_urls:
                                            file_urls.append(src)
                            
                            # Добавляем ссылки из сетевых запросов (они уже отфильтрованы по качеству)
                            for url in video_urls_found:
                                if _is_hls_m3u8_url(url):
                                    if url not in hls_urls:
                                        hls_urls.append(url)
                                        quality = quality_filter if quality_filter else (_guess_quality_from_text(url) or "")
                                        hls_streams.append({"url": url, "quality": quality})
                                elif _is_direct_video_url(url):
                                    if url not in file_urls:
                                        file_urls.append(url)
                            
                            # Получаем заголовок страницы
                            try:
                                browser_title = page.title()
                                if browser_title and not page_title:
                                    page_title = browser_title
                            except Exception:
                                pass
                                
                        except PlaywrightTimeoutError:
                            logger.warning("Таймаут при загрузке страницы beeg.com")
                        except Exception as exc:
                            logger.debug("Ошибка при извлечении ссылок через Playwright: %s", exc)
                        finally:
                            context.close()
                            browser.close()
                except Exception as exc:
                    logger.warning("Не удалось использовать Playwright напрямую: %s", exc)
                    # Fallback на стандартный браузерный парсер
                    browser_hls, browser_files, browser_title, _, browser_streams = fetch_media_urls_with_browser(
                        page_url,
                        cookies_path=cookies_path,
                        proxy=proxy,
                        translation_hash=quality_filter,
                    )
                    hls_urls.extend(browser_hls)
                    file_urls.extend(browser_files)
                    hls_streams.extend(browser_streams)
                    if browser_title and not page_title:
                        page_title = browser_title
            else:
                # Fallback на стандартный браузерный парсер
                browser_hls, browser_files, browser_title, _, browser_streams = fetch_media_urls_with_browser(
                    page_url,
                    cookies_path=cookies_path,
                    proxy=proxy,
                    translation_hash=quality_filter,
                )
                hls_urls.extend(browser_hls)
                file_urls.extend(browser_files)
                hls_streams.extend(browser_streams)
                if browser_title and not page_title:
                    page_title = browser_title
            
            # Если браузерный парсер нашёл потоки с качеством, обновляем список качеств
            if hls_streams:
                for stream in hls_streams:
                    stream_quality = stream.get("quality", "").strip()
                    if stream_quality and not any(q.get("name", "").lower() == stream_quality.lower() for q in qualities):
                        qualities.append({
                            "name": stream_quality,
                            "hash": stream_quality,
                            "quality": stream_quality,
                        })
        except Exception as exc:
            logger.warning("Браузерный парсер не смог извлечь ссылки: %s", exc)
            # Продолжаем работу, возможно статический парсинг найдёт что-то

        # Дополнительно пробуем извлечь ссылки статически из HTML
        # Ищем прямые ссылки на видеофайлы в JavaScript коде
        video_url_patterns = [
            r'["\']src["\']\s*:\s*["\']([^"\']+\.(?:mp4|webm|mkv|mov|avi|flv|m3u8))["\']',
            r'["\']file["\']\s*:\s*["\']([^"\']+\.(?:mp4|webm|mkv|mov|avi|flv|m3u8))["\']',
            r'["\']url["\']\s*:\s*["\']([^"\']+\.(?:mp4|webm|mkv|mov|avi|flv|m3u8))["\']',
            r'(https?://[^\s"\'>]+\.(?:mp4|webm|mkv|mov|avi|flv|m3u8))',
        ]
        
        for script in soup.find_all("script"):
            script_text = script.string or ""
            if not script_text:
                continue
            
            for pattern in video_url_patterns:
                for match in re.finditer(pattern, script_text, re.IGNORECASE):
                    found_url = match.group(1)
                    if not found_url or len(found_url) < 10:
                        continue
                    full_url = urljoin(page_url, found_url)
                    if _is_hls_m3u8_url(full_url):
                        if full_url not in hls_urls:
                            hls_urls.append(full_url)
                            quality = _guess_quality_from_text(full_url) or ""
                            hls_streams.append({"url": full_url, "quality": quality})
                    elif _is_direct_video_url(full_url):
                        if full_url not in file_urls:
                            file_urls.append(full_url)

        # Если не найдено качеств, но есть потоки, создаём качества на основе потоков
        if not qualities and hls_streams:
            for stream in hls_streams:
                stream_quality = stream.get("quality", "").strip()
                if stream_quality and not any(q.get("name") == stream_quality for q in qualities):
                    qualities.append({
                        "name": stream_quality,
                        "hash": stream_quality,
                        "quality": stream_quality,
                    })

        # Если всё ещё нет качеств, но есть ссылки, создаём дефолтное качество
        if not qualities and (hls_urls or file_urls):
            qualities.append({
                "name": "Auto",
                "hash": "Auto",
                "quality": "Auto",
            })

        return (
            hls_urls,
            file_urls,
            page_title,
            qualities if qualities else None,
            hls_streams,
        )

