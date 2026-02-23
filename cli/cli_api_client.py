"""
CLI-клиент для GrabVidZilla, работающий через HTTP API.

Использует click для команд и rich для красивого вывода.
Все операции выполняются через REST API (FastAPI).
"""

from __future__ import annotations

import os
import re
import sys
import time
from typing import Optional, Tuple, Dict, List
from urllib.parse import urlparse
from collections import OrderedDict

import click
import requests
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, SpinnerColumn

# URL API по умолчанию
API_BASE_URL = os.getenv("GVZ_API_URL", "http://localhost:8000")

console = Console()


def _parse_proxy_url(proxy_url: Optional[str]) -> Tuple[Optional[Dict[str, str]], Optional[Dict[str, str]], Optional[str]]:
    """
    Возвращает (query_params, playwright_proxy, error_message).
    query_params используется для HTTP-запроса к API, playwright_proxy — для локального отображения.
    """
    if not proxy_url:
        return None, None, None

    try:
        parsed = urlparse(proxy_url)
    except Exception:
        return None, None, "Некорректный формат прокси URL."

    if not parsed.scheme or not parsed.hostname:
        return None, None, "Прокси URL должен содержать схему и хост."

    server = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        server += f":{parsed.port}"

    query_params: Dict[str, str] = {"proxy_server": server}
    playwright_proxy: Dict[str, str] = {"server": server}

    if parsed.username:
        query_params["proxy_username"] = parsed.username
        playwright_proxy["username"] = parsed.username
    if parsed.password:
        query_params["proxy_password"] = parsed.password
        playwright_proxy["password"] = parsed.password

    return query_params, playwright_proxy, None


def _build_format_selector(selected_quality: str) -> str:
    """
    Возвращает строку формата для yt-dlp на основе выбранного качества.
    """
    if selected_quality == "audio only":
        return "bestaudio/best"
    try:
        if selected_quality.endswith("p"):
            h = int(selected_quality[:-1])
            return f"bv*[height<={h}]+ba/best[height<={h}]"
    except Exception:
        pass
    return "bv*+ba/best"


def _format_speed(bps: Optional[float]) -> str:
    """Форматирует скорость в человекочитаемый вид."""
    if not bps or bps <= 0:
        return "—"
    units = ["Б/с", "КБ/с", "МБ/с", "ГБ/с"]
    i = 0
    v = float(bps)
    while v >= 1024.0 and i < len(units) - 1:
        v /= 1024.0
        i += 1
    return f"{v:.1f} {units[i]}"


def _format_size(num_bytes: Optional[int]) -> str:
    """Форматирует размер в человекочитаемый вид."""
    if not num_bytes or num_bytes <= 0:
        return "—"
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    i = 0
    v = float(num_bytes)
    while v >= 1024.0 and i < len(units) - 1:
        v /= 1024.0
        i += 1
    if units[i] in ("МБ", "ГБ", "ТБ"):
        return f"{v:.1f} {units[i]}"
    return f"{int(v)} {units[i]}"


def _format_duration(seconds: Optional[float]) -> str:
    """Форматирует длительность в человекочитаемый вид."""
    if not seconds or seconds <= 0:
        return "—"
    if seconds >= 60.0:
        m = int(seconds // 60)
        s = seconds - (m * 60)
        if s >= 10:
            return f"{m} мин {int(s)} сек"
        return f"{m} мин {s:.1f} сек"
    return f"{seconds:.1f} сек"


def _run_download_via_api(
    url: str,
    cookies_path: Optional[str] = None,
    fmt: Optional[str] = None,
    audio_only: bool = False,
    subtitle_lang: Optional[str] = None,
) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Выполняет загрузку через API, показывая прогресс через rich.Progress.

    Returns:
        Кортеж (success, filename, checksum_sha256):
        - success: True при успешной загрузке, False при ошибке;
        - filename: имя загруженного файла (если известен);
        - checksum_sha256: контрольная сумма файла.
    """
    try:
        # Отправляем запрос на скачивание
        payload = {
            "url": url,
            "format": fmt if not audio_only else None,
            "audio_only": audio_only,
        }
        if cookies_path:
            payload["cookies_path"] = cookies_path
        if subtitle_lang:
            payload["subtitle_lang"] = subtitle_lang

        response = requests.post(f"{API_BASE_URL}/downloads", json=payload, timeout=10)
        response.raise_for_status()
        task_data = response.json()
        task_id = task_data["id"]

        console.print(":rocket: [bold]Старт загрузки[/bold]", style="cyan")
        started_at = time.perf_counter()

        # Отслеживаем прогресс
        with Progress(
            SpinnerColumn(),
            BarColumn(bar_width=40),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("{task.description}"),
            TextColumn("{task.fields[speed]}"),
            console=console,
            transient=True,
            refresh_per_second=4,  # Обновляем 4 раза в секунду для плавности
        ) as progress:
            task_id_progress = progress.add_task(
                "[cyan]Загрузка...[/cyan]",
                total=100,
                speed="—"
            )

            last_progress = 0.0
            while True:
                try:
                    status_response = requests.get(f"{API_BASE_URL}/downloads/{task_id}", timeout=5)
                    status_response.raise_for_status()
                    task_status = status_response.json()

                    state = task_status.get("status", "unknown")
                    progress_pct = task_status.get("progress", 0.0)
                    speed_str_raw = task_status.get("speed")
                    filename = task_status.get("filename")
                    error_message = task_status.get("error_message")
                    error_type = task_status.get("error_type")
                    file_size = task_status.get("file_size")

                    # Обновляем прогресс при каждом опросе для плавного отображения
                    speed_display = speed_str_raw or "—"
                    progress.update(
                        task_id_progress,
                        completed=progress_pct,
                        description="[cyan]Загрузка...[/cyan]",
                        speed=speed_display,
                    )
                    last_progress = progress_pct

                    if state == "completed":
                        progress.update(task_id_progress, completed=100, speed=speed_display)
                        elapsed = time.perf_counter() - started_at
                        console.print()  # Новая строка после прогресс-бара
                        console.print(f":white_check_mark: [bold green]Готово[/bold green]: {filename}")
                        console.print(f"[dim]Время скачивания: {_format_duration(elapsed)}[/dim]")

                        # Размер файла
                        if file_size:
                            console.print(f"[dim]Размер файла: {_format_size(file_size)}[/dim]")
                        checksum_hex = task_status.get("sha256")
                        if checksum_hex:
                            console.print(f"[dim]SHA-256: {checksum_hex}[/dim]")
                        console.print()  # отступ после успешного завершения
                        return True, filename, checksum_hex

                    elif state == "error":
                        console.print()  # Новая строка после прогресс-бара
                        err_text = error_message or "Неизвестная ошибка"
                        if error_type:
                            err_text = f"[{error_type}] {err_text}"
                        console.print(f":boom: [bold red]Ошибка[/bold red]: {err_text}")
                        return False, None, None

                    elif state == "cancelled":
                        console.print()  # Новая строка после прогресс-бара
                        console.print("[yellow]Загрузка отменена[/yellow]")
                        return False, None, None

                    # Ждём перед следующим опросом (опрашиваем чаще для плавного обновления)
                    time.sleep(0.5)

                except requests.exceptions.RequestException as e:
                    console.print(f"[red]Ошибка получения статуса: {e}[/red]")
                    return False, None, None

    except requests.exceptions.RequestException as e:
        error_msg = str(e)
        if hasattr(e, "response") and e.response is not None:
            try:
                detail = e.response.json().get("detail", error_msg)
                error_msg = detail
            except Exception:
                error_msg = f"HTTP {e.response.status_code}: {error_msg}"
        console.print(f":boom: [bold red]Ошибка[/bold red]: {error_msg}")
        return False, None, None
    except Exception as exc:
        console.print(f":boom: [bold red]Ошибка[/bold red]: {exc}")
        return False, None, None


def _show_menu_and_handle(
    use_browser_parser: bool,
    browser_proxy_url: Optional[str],
) -> None:
    """
    Простое интерактивное меню, работающее через HTTP API.
    """
    while True:
        console.print()  # отступ перед показом меню
        console.print("[bold]GrabVidZilla API Client[/bold] — кроссплатформенный загрузчик видео")
        console.print(f"[dim]API: {API_BASE_URL}[/dim]")
        console.print("1. Скачать видео")
        console.print("2. help")
        console.print("3. Загрузить cookies")
        console.print("4. Найти видео на странице")
        console.print("5. Поиск видео на специфичных сайтах")
        console.print("0. Выход")
        choice = click.prompt("Выберите пункт", type=int, default=1)

        if choice == 1:
            url = click.prompt("Введите URL видео", type=str)
            try:
                # Если есть cookies в tools/cookies.txt — используем их
                project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
                default_cookies = os.path.join(project_root, "tools", "cookies.txt")
                use_cookies = default_cookies if os.path.isfile(default_cookies) else None

                # Анализируем через API
                try:
                    params = {"url": url}
                    if use_cookies:
                        params["cookies_path"] = use_cookies
                    response = requests.get(f"{API_BASE_URL}/analyze", params=params, timeout=30)
                    response.raise_for_status()
                    data = response.json()
                    info = data["info"]
                    qualities = data["qualities"]
                    subtitle_langs = data.get("subtitle_langs", [])
                    if not qualities:
                        qualities = ["best"]
                except requests.exceptions.RequestException as e:
                    console.print(f"[yellow]Не удалось выполнить анализ ({e}).[/yellow]")
                    # Предложим загрузить/указать cookies и попробовать снова
                    try_again = click.confirm(
                        "Использовать cookies (tools/cookies.txt) или указать путь и попробовать снова?",
                        default=True
                    )
                    if try_again:
                        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
                        tools_dir = os.path.join(project_root, "tools")
                        os.makedirs(tools_dir, exist_ok=True)
                        default_cookies = os.path.join(tools_dir, "cookies.txt")
                        if not os.path.isfile(default_cookies):
                            path = click.prompt("Путь к cookies.txt (Netscape)", type=str)
                            if os.path.isfile(path):
                                import shutil
                                shutil.copyfile(path, default_cookies)
                                console.print(f"[green]Cookies сохранены[/green]: {default_cookies}")
                        use_cookies = default_cookies if os.path.isfile(default_cookies) else None
                        try:
                            params = {"url": url}
                            if use_cookies:
                                params["cookies_path"] = use_cookies
                            response = requests.get(f"{API_BASE_URL}/analyze", params=params, timeout=30)
                            response.raise_for_status()
                            data = response.json()
                            qualities = data["qualities"]
                            subtitle_langs = data.get("subtitle_langs", [])
                            if not qualities:
                                qualities = ["best"]
                        except Exception as e2:
                            console.print(f"[yellow]Повторный анализ не удался ({e2}). Будет использовано качество по умолчанию.[/yellow]")
                            qualities = ["best"]
                    else:
                        qualities = ["best"]
                    subtitle_langs = []

                # Показ списка качеств
                console.print("[bold]Доступные качества:[/bold]")
                for idx, q in enumerate(qualities, start=1):
                    console.print(f"  {idx}. {q}")
                try:
                    choice_q = click.prompt("Выберите качество (номер)", type=int, default=1)
                    if 1 <= choice_q <= len(qualities):
                        selected_quality = qualities[choice_q - 1]
                    else:
                        selected_quality = qualities[0]
                except Exception:
                    selected_quality = qualities[0]

                # Выбор субтитров (если доступны)
                subtitle_lang = None
                if subtitle_langs:
                    console.print("[bold]Доступные языки субтитров:[/bold]")
                    for idx, lang in enumerate(subtitle_langs, start=1):
                        console.print(f"  {idx}. {lang}")
                    try:
                        choice_sub = click.prompt("Выберите язык субтитров (номер, 0 = без субтитров)", type=int, default=0)
                        if 1 <= choice_sub <= len(subtitle_langs):
                            subtitle_lang = subtitle_langs[choice_sub - 1]
                    except Exception:
                        pass

                fmt = _build_format_selector(selected_quality)
                audio_only = (selected_quality == "audio only")

                _run_download_via_api(
                    url=url,
                    cookies_path=use_cookies,
                    fmt=fmt,
                    audio_only=audio_only,
                    subtitle_lang=subtitle_lang,
                )
            except Exception as exc:
                console.print(f":boom: [bold red]Ошибка[/bold red]: {exc}")
                pass
            finally:
                console.print()  # отступ перед возвратом к меню

        elif choice == 2:
            # Показать help
            console.print()
            console.print("[bold]GrabVidZilla API Client[/bold]")
            console.print()
            console.print("Использование:")
            console.print("  python -m cli.cli_api_client")
            console.print()
            console.print("Переменные окружения:")
            console.print(f"  GVZ_API_URL — URL API сервера (по умолчанию: {API_BASE_URL})")
            console.print()
            console.print("[cyan]0. Вернуться в меню[/cyan]")
            _ = click.prompt("Нажмите 0 для возврата", type=int, default=0)
            console.print()
            continue

        elif choice == 3:
            # Загрузка/обновление cookies в tools/cookies.txt c объединением записей.
            import shutil
            from http.cookiejar import MozillaCookieJar

            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            tools_dir = os.path.join(project_root, "tools")
            os.makedirs(tools_dir, exist_ok=True)
            src = click.prompt("Путь к cookies.txt (Netscape формат)", type=str)
            if not os.path.isfile(src):
                console.print("[red]Файл не найден[/red]")
                console.print()
                continue

            dst = os.path.join(tools_dir, "cookies.txt")

            def _load_jar(path: str) -> MozillaCookieJar:
                jar = MozillaCookieJar()
                if os.path.isfile(path):
                    try:
                        jar.load(path, ignore_discard=True, ignore_expires=True)
                    except Exception:
                        # Если существующий файл повреждён или не читается —
                        # начинаем с пустого набора cookies.
                        jar = MozillaCookieJar()
                return jar

            try:
                # Текущие cookies (если есть)
                existing = _load_jar(dst)
                # Новые cookies из выбранного файла
                incoming = MozillaCookieJar()
                incoming.load(src, ignore_discard=True, ignore_expires=True)

                # Объединяем: ключом считаем (domain, path, name).
                by_key: dict[tuple[str, str, str], object] = {}
                for c in existing:
                    key = (c.domain, c.path, c.name)  # type: ignore[attr-defined]
                    by_key[key] = c
                for c in incoming:
                    key = (c.domain, c.path, c.name)  # type: ignore[attr-defined]
                    by_key[key] = c  # новые перезаписывают старые

                merged = MozillaCookieJar()
                for c in by_key.values():
                    merged.set_cookie(c)  # type: ignore[arg-type]

                merged.save(dst, ignore_discard=True, ignore_expires=True)
                console.print(f"[green]Cookies обновлены и сохранены[/green]: {dst}")
            except Exception as e:
                console.print(f"[red]Не удалось объединить cookies[/red]: {e}")

            console.print()
            continue

        elif choice == 4:
            page_url = click.prompt("Введите URL страницы", type=str)

            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            default_cookies = os.path.join(project_root, "tools", "cookies.txt")
            use_cookies = default_cookies if os.path.isfile(default_cookies) else None

            base_params: Dict[str, str] = {
                "url": page_url,
                "use_browser": "false",
                "fallback_to_browser": "false",
            }
            if use_cookies:
                base_params["cookies_path"] = use_cookies

            def _request_media(extra: Dict[str, str], timeout: int = 30) -> dict:
                params = dict(base_params)
                params.update(extra)
                response = requests.get(f"{API_BASE_URL}/media", params=params, timeout=timeout)
                response.raise_for_status()
                return response.json()

            def _parse_payload(payload: dict) -> tuple[list[str], list[str], bool, Optional[str], list[dict[str, str]] | None, list[dict[str, str]] | None]:
                return (
                    payload.get("hls_urls", []),
                    payload.get("file_urls", []),
                    payload.get("used_browser", False),
                    payload.get("page_title"),
                    payload.get("translations"),
                    payload.get("hls_streams"),
                )

            try:
                data = _request_media({})
            except requests.exceptions.RequestException as e:
                console.print(f"[red]Не удалось получить список ссылок[/red]: {e}")
                console.print()
                continue

            hls_urls, file_urls, used_browser, page_title, translations, hls_streams = _parse_payload(data)
            selected_translation_hash: Optional[str] = None
            selected_translation_name: Optional[str] = None

            if translations:
                console.print("[bold]Доступные переводы:[/bold]")
                for idx, item in enumerate(translations, start=1):
                    console.print(f"  {idx}. {item.get('name', 'Без названия')}")
                console.print("  0. Отмена")
                try:
                    translation_choice = click.prompt("Выберите перевод", type=int, default=1)
                except Exception:
                    console.print("[red]Некорректный выбор.[/red]")
                    console.print()
                    continue
                if translation_choice == 0:
                    console.print("[dim]Выбор перевода отменён пользователем.[/dim]")
                    console.print()
                    continue
                if 1 <= translation_choice <= len(translations):
                    chosen = translations[translation_choice - 1]
                    selected_translation_hash = chosen.get("hash")
                    selected_translation_name = chosen.get("name")
                    try:
                        data = _request_media(
                            {"translation_hash": selected_translation_hash} if selected_translation_hash else {}
                        )
                    except requests.exceptions.RequestException as e:
                        console.print(f"[red]Не удалось получить ссылки для выбранного перевода[/red]: {e}")
                        console.print()
                        continue
                    hls_urls, file_urls, used_browser, page_title, _, hls_streams = _parse_payload(data)
                else:
                    console.print("[red]Номер перевода вне диапазона.[/red]")
                    console.print()
                    continue

            if not hls_urls and not file_urls and use_browser_parser:
                console.print("[cyan]Статический парсер API ничего не нашёл. Запрашиваем браузерный режим (Playwright)...[/cyan]")
                proxy_query, _proxy_local, proxy_error = _parse_proxy_url(browser_proxy_url)
                if proxy_error:
                    console.print(f"[yellow]{proxy_error}[/yellow]")
                    proxy_query = None

                browser_params: Dict[str, str] = {
                    "use_browser": "true",
                    "fallback_to_browser": "false",
                }
                if proxy_query:
                    browser_params.update(proxy_query)
                if selected_translation_hash:
                    browser_params["translation_hash"] = selected_translation_hash

                try:
                    data_browser = _request_media(browser_params, timeout=60)
                except requests.exceptions.RequestException as e:
                    console.print(f"[yellow]Браузерный парсер не доступен[/yellow]: {e}")
                    console.print()
                    continue

                browser_hls, browser_files, used_browser, browser_title, browser_translations, browser_streams = _parse_payload(data_browser)
                if browser_title:
                    page_title = browser_title
                if browser_translations:
                    translations = browser_translations
                if browser_streams:
                    hls_streams = browser_streams
                hls_urls = browser_hls
                file_urls = browser_files

            if not hls_urls and not file_urls:
                console.print("[yellow]Не удалось найти медиа-ссылки через API.[/yellow]")
                console.print()
                continue

            def _select_quality(streams: Optional[List[Dict[str, str]]]) -> tuple[List[Dict[str, str]], Optional[str]]:
                if not streams:
                    return [], None
                quality_map: "OrderedDict[str, List[Dict[str, str]]]" = OrderedDict()
                for stream in streams:
                    quality_label = stream.get("quality") or ""
                    label = quality_label or "Неизвестно"
                    quality_map.setdefault(label, []).append(stream)
                if len(quality_map) == 1:
                    label = next(iter(quality_map))
                    return quality_map[label], label if label else None
                console.print("[bold]Доступные качества (HLS):[/bold]")
                quality_labels = list(quality_map.keys())
                for idx, label in enumerate(quality_labels, start=1):
                    console.print(f"  {idx}. {label or 'Неизвестно'}")
                console.print("  0. Отмена")
                try:
                    quality_choice = click.prompt("Выберите качество", type=int, default=1)
                except Exception:
                    console.print("[red]Некорректный выбор.[/red]")
                    console.print()
                    return [], None
                if quality_choice == 0:
                    console.print("[dim]Выбор качества отменён пользователем.[/dim]")
                    console.print()
                    return [], None
                if 1 <= quality_choice <= len(quality_labels):
                    label = quality_labels[quality_choice - 1]
                    return quality_map[label], label if label else None
                console.print("[red]Номер качества вне диапазона.[/red]")
                console.print()
                return [], None

            quality_streams, selected_quality_label = _select_quality(hls_streams)
            if hls_streams and not quality_streams:
                continue
            if quality_streams:
                hls_urls = [stream.get("url") or "" for stream in quality_streams if stream.get("url")]
                hls_urls = [u for u in hls_urls if u]

            if used_browser:
                console.print("[dim]Результаты получены с помощью браузерного парсера.[/dim]")
            if page_title:
                console.print(f"[bold]{page_title}[/bold]")
            if selected_translation_name:
                console.print(f"[bold]Перевод: {selected_translation_name}[/bold]")
            if selected_quality_label:
                console.print(f"[bold]Качество: {selected_quality_label}[/bold]")

            console.print("[bold]Найденные видео и потоки:[/bold]")
            indexed: list[dict[str, Optional[str]]] = []

            if quality_streams:
                console.print("HLS (m3u8):")
                for idx, stream in enumerate(quality_streams, start=1):
                    url_value = stream.get("url") or ""
                    if not url_value:
                        continue
                    label = f"HLS #{idx}"
                    quality_label = stream.get("quality") or "Неизвестно"
                    console.print(f"  {len(indexed)+1}. {label} ({quality_label}): {url_value}")
                    indexed.append(
                        {
                            "label": label,
                            "url": url_value,
                            "title": page_title or label,
                            "translation_name": selected_translation_name,
                            "quality": stream.get("quality"),
                        }
                    )
            else:
                if hls_urls:
                    console.print("HLS (m3u8):")
                    for idx, u in enumerate(hls_urls, start=1):
                        label = f"HLS #{idx}"
                        console.print(f"  {len(indexed)+1}. {label}: {u}")
                        indexed.append(
                            {
                                "label": label,
                                "url": u,
                                "title": page_title or label,
                                "translation_name": selected_translation_name,
                                "quality": selected_quality_label,
                            }
                        )

            if file_urls:
                console.print("Файлы:")
                for idx, u in enumerate(file_urls, start=1):
                    label = f"FILE #{idx}"
                    console.print(f"  {len(indexed)+1}. {label}: {u}")
                    indexed.append(
                        {
                            "label": label,
                            "url": u,
                            "title": page_title or label,
                            "translation_name": selected_translation_name,
                            "quality": None,
                        }
                    )

            if not indexed:
                console.print("[yellow]Не удалось собрать список ссылок для загрузки.[/yellow]")
                console.print()
                continue

            total_items = len(indexed)
            all_option = total_items + 1

            console.print()
            console.print(f"[cyan]{all_option}. Скачать все найденные[/cyan]")
            console.print("[cyan]0. Отмена[/cyan]")

            try:
                choice_idx = click.prompt(
                    "Выберите номер видео для загрузки", type=int, default=1
                )
            except Exception:
                console.print("[red]Некорректный выбор.[/red]")
                console.print()
                continue

            if choice_idx == 0:
                console.print("[dim]Загрузка отменена пользователем.[/dim]")
                console.print()
                continue

            if choice_idx == all_option:
                import time as _time

                results: list[dict] = []
                batch_started = _time.perf_counter()

                for entry in indexed:
                    label = entry.get("label") or ""
                    selected_url = entry.get("url") or ""
                    translation_name = entry.get("translation_name")
                    quality_name = entry.get("quality")
                    console.print(f"[cyan]Скачиваем {label}[/cyan]")
                    if translation_name:
                        console.print(f"[dim]Перевод: {translation_name}[/dim]")
                    if quality_name:
                        console.print(f"[dim]Качество: {quality_name}[/dim]")
                    success, file_name, checksum_hex = _run_download_via_api(selected_url, cookies_path=use_cookies)
                    results.append(
                        {
                            "label": label,
                            "success": success,
                            "filename": file_name or "(нет файла)",
                            "translation": translation_name,
                            "quality": quality_name,
                            "checksum": checksum_hex,
                        }
                    )

                total_elapsed = _time.perf_counter() - batch_started
                console.print()
                console.print("[bold]Отчёт по пакетной загрузке:[/bold]")
                for r in results:
                    status = "[green]успешно[/green]" if r["success"] else "[red]ошибка[/red]"
                    translation_text = f", перевод: {r['translation']}" if r.get("translation") else ""
                    quality_text = f", качество: {r['quality']}" if r.get("quality") else ""
                    checksum_text = f", SHA-256: {r['checksum']}" if r.get("checksum") else ""
                    console.print(f"- {r['label']}: {status}, файл: {r['filename']}{translation_text}{quality_text}{checksum_text}")
                console.print(f"[bold]Итого[/bold]: {total_elapsed:.1f} сек")
                console.print()
                continue

            if not (1 <= choice_idx <= total_items):
                console.print("[red]Номер вне диапазона.[/red]")
                console.print()
                continue

            entry = indexed[choice_idx - 1]
            selected_url = entry.get("url") or ""
            translation_name = entry.get("translation_name")
            quality_name = entry.get("quality")
            if translation_name:
                console.print(f"[dim]Перевод: {translation_name}[/dim]")
            if quality_name:
                console.print(f"[dim]Качество: {quality_name}[/dim]")
            _run_download_via_api(selected_url, cookies_path=use_cookies)
            console.print()
            continue

        elif choice == 5:
            # Поиск видео на специфичных сайтах через REST API с автоопределением адаптера
            page_url = click.prompt("Введите URL страницы", type=str)

            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            default_cookies = os.path.join(project_root, "tools", "cookies.txt")
            use_cookies = default_cookies if os.path.isfile(default_cookies) else None

            # Запрос к API с use_adapter=true
            adapter_params: Dict[str, str] = {
                "url": page_url,
                "use_adapter": "true",
            }
            if use_cookies:
                adapter_params["cookies_path"] = use_cookies

            def _request_adapter_media(
                extra: Dict[str, str], timeout: int = 30
            ) -> dict:
                params = dict(adapter_params)
                params.update(extra)
                resp = requests.get(
                    f"{API_BASE_URL}/media", params=params, timeout=timeout
                )
                resp.raise_for_status()
                return resp.json()

            try:
                data = _request_adapter_media({})
            except requests.exceptions.RequestException as e:
                console.print(f"[red]Не удалось получить данные через API[/red]: {e}")
                console.print()
                continue

            adapter_name = data.get("adapter_name")
            if not adapter_name:
                console.print("[yellow]Для данного сайта не найден специфичный адаптер.[/yellow]")
                console.print("[dim]Попробуйте использовать пункт 4 'Найти видео на странице' для универсального парсинга.[/dim]")
                console.print()
                continue

            console.print(f"[cyan]Найден адаптер: {adapter_name}[/cyan]")
            console.print("[dim]Обработка через site-specific парсер...[/dim]")

            hls_urls = data.get("hls_urls", [])
            file_urls = data.get("file_urls", [])
            page_title = data.get("page_title")
            translations = data.get("translations")
            hls_streams = data.get("hls_streams")
            selected_translation_hash: Optional[str] = None
            selected_translation_name: Optional[str] = None

            if translations:
                # Определяем, являются ли это качествами или переводами
                is_quality_list = False
                quality_pattern = re.compile(r'^\d+p$|^auto$', re.IGNORECASE)
                if all(
                    item.get("quality") or quality_pattern.match(item.get("name", ""))
                    for item in translations
                ):
                    is_quality_list = True

                if is_quality_list:
                    console.print("[bold]Доступные качества:[/bold]")
                else:
                    console.print("[bold]Доступные переводы:[/bold]")

                for idx, item in enumerate(translations, start=1):
                    console.print(f"  {idx}. {item.get('name', 'Без названия')}")
                console.print("  0. Отмена")
                try:
                    if is_quality_list:
                        console.print("[dim]Подсказка: выберите максимальное качество (1) для лучшего качества[/dim]")
                        translation_choice = click.prompt("Выберите качество", type=int, default=1)
                    else:
                        translation_choice = click.prompt("Выберите перевод", type=int, default=1)
                except Exception:
                    console.print("[red]Некорректный выбор.[/red]")
                    console.print()
                    continue
                if translation_choice == 0:
                    if is_quality_list:
                        console.print("[dim]Выбор качества отменён пользователем.[/dim]")
                    else:
                        console.print("[dim]Выбор перевода отменён пользователем.[/dim]")
                    console.print()
                    continue
                if 1 <= translation_choice <= len(translations):
                    selected = translations[translation_choice - 1]
                    selected_translation_hash = selected.get("hash")
                    selected_translation_name = selected.get("name")
                    try:
                        extra: Dict[str, str] = {}
                        if selected_translation_hash:
                            extra["translation_hash"] = selected_translation_hash
                        data = _request_adapter_media(extra)
                    except requests.exceptions.RequestException as e:
                        if is_quality_list:
                            console.print(f"[red]Не удалось получить ссылки для выбранного качества[/red]: {e}")
                        else:
                            console.print(f"[red]Не удалось получить ссылки для выбранного перевода[/red]: {e}")
                        console.print()
                        continue
                    hls_urls = data.get("hls_urls", [])
                    file_urls = data.get("file_urls", [])
                    page_title = data.get("page_title")
                    hls_streams = data.get("hls_streams")
                else:
                    if is_quality_list:
                        console.print("[red]Номер качества вне диапазона.[/red]")
                    else:
                        console.print("[red]Номер перевода вне диапазона.[/red]")
                    console.print()
                    continue

            if not hls_urls and not file_urls:
                console.print("[yellow]На странице не найдено ни одного видео или HLS-потока.[/yellow]")
                console.print()
                continue

            # Выбор качества из HLS потоков
            def _select_quality_5(
                streams: Optional[List[Dict[str, str]]],
            ) -> tuple[List[Dict[str, str]], Optional[str]]:
                if not streams:
                    return [], None
                quality_map: "OrderedDict[str, List[Dict[str, str]]]" = OrderedDict()
                for stream in streams:
                    ql = stream.get("quality") or ""
                    label = ql or "Неизвестно"
                    quality_map.setdefault(label, []).append(stream)

                # Если только одно качество и один поток — возвращаем без запроса
                if len(quality_map) == 1 and len(streams) == 1:
                    label = next(iter(quality_map))
                    return quality_map[label], label if label else None

                console.print("[bold]Доступные качества (HLS):[/bold]")
                quality_labels = list(quality_map.keys())
                for idx, label in enumerate(quality_labels, start=1):
                    count = len(quality_map[label])
                    quality_display = label if label and label != "Неизвестно" else "Неизвестно"
                    if count > 1:
                        console.print(f"  {idx}. {quality_display} ({count} потоков)")
                    else:
                        console.print(f"  {idx}. {quality_display}")
                console.print("  0. Отмена")
                try:
                    quality_choice = click.prompt("Выберите качество", type=int, default=1)
                except Exception:
                    console.print("[red]Некорректный выбор.[/red]")
                    console.print()
                    return [], None
                if quality_choice == 0:
                    console.print("[dim]Выбор качества отменён пользователем.[/dim]")
                    console.print()
                    return [], None
                if 1 <= quality_choice <= len(quality_labels):
                    label = quality_labels[quality_choice - 1]
                    selected_entries = quality_map[label]
                    # Если выбрано качество с несколькими потоками — предлагаем выбрать конкретный
                    if len(selected_entries) > 1:
                        console.print(f"[bold]Доступные потоки для качества '{label or 'Неизвестно'}':[/bold]")
                        for idx, entry in enumerate(selected_entries, start=1):
                            url_preview = entry.get("url", "")[:80] + "..." if len(entry.get("url", "")) > 80 else entry.get("url", "")
                            console.print(f"  {idx}. Поток #{idx}: {url_preview}")
                        console.print("  0. Отмена")
                        try:
                            stream_choice = click.prompt("Выберите поток", type=int, default=1)
                        except Exception:
                            console.print("[red]Некорректный выбор.[/red]")
                            console.print()
                            return [], None
                        if stream_choice == 0:
                            console.print("[dim]Выбор потока отменён пользователем.[/dim]")
                            console.print()
                            return [], None
                        if 1 <= stream_choice <= len(selected_entries):
                            return [selected_entries[stream_choice - 1]], label if label else None
                        console.print("[red]Номер потока вне диапазона.[/red]")
                        console.print()
                        return [], None
                    return selected_entries, label if label else None
                console.print("[red]Номер качества вне диапазона.[/red]")
                console.print()
                return [], None

            selected_quality_label: Optional[str] = None
            quality_streams: List[Dict[str, str]] = hls_streams or []
            if hls_streams:
                quality_streams, selected_quality_label = _select_quality_5(hls_streams)
                if not quality_streams:
                    continue
                hls_urls = [s.get("url") or "" for s in quality_streams if s.get("url")]

            if page_title:
                console.print(f"[bold]{page_title}[/bold]")
            if selected_translation_name:
                console.print(f"[bold]Перевод: {selected_translation_name}[/bold]")
            if selected_quality_label:
                console.print(f"[bold]Качество: {selected_quality_label}[/bold]")

            # Формируем единый нумерованный список для выбора
            console.print("[bold]Найденные видео и потоки:[/bold]")
            indexed: list[dict[str, Optional[str]]] = []

            if quality_streams:
                console.print("HLS (m3u8):")
                for idx, stream in enumerate(quality_streams, start=1):
                    url_value = stream.get("url") or ""
                    if not url_value:
                        continue
                    label = f"HLS #{idx}"
                    quality_label = stream.get("quality") or "Неизвестно"
                    console.print(f"  {len(indexed)+1}. {label} ({quality_label}): {url_value}")
                    indexed.append(
                        {
                            "label": label,
                            "url": url_value,
                            "title": page_title or label,
                            "translation_name": selected_translation_name,
                            "quality": stream.get("quality"),
                        }
                    )
            elif hls_urls:
                console.print("HLS (m3u8):")
                for idx, u in enumerate(hls_urls, start=1):
                    label = f"HLS #{idx}"
                    console.print(f"  {len(indexed)+1}. {label}: {u}")
                    indexed.append(
                        {
                            "label": label,
                            "url": u,
                            "title": page_title or label,
                            "translation_name": selected_translation_name,
                            "quality": selected_quality_label,
                        }
                    )

            if file_urls:
                console.print("Файлы:")
                for idx, u in enumerate(file_urls, start=1):
                    if not u:
                        continue
                    label = f"FILE #{idx}"
                    console.print(f"  {len(indexed)+1}. {label}: {u}")
                    indexed.append(
                        {
                            "label": label,
                            "url": u,
                            "title": page_title or label,
                            "translation_name": selected_translation_name,
                            "quality": None,
                        }
                    )

            if not indexed:
                console.print("[yellow]Не удалось собрать список ссылок для загрузки.[/yellow]")
                console.print()
                continue

            total_items = len(indexed)
            all_option = total_items + 1

            console.print()
            console.print(f"[cyan]{all_option}. Скачать все найденные[/cyan]")
            console.print("[cyan]0. Отмена[/cyan]")

            try:
                choice_idx = click.prompt(
                    "Выберите номер видео для загрузки", type=int, default=1
                )
            except Exception:
                console.print("[red]Некорректный выбор.[/red]")
                console.print()
                continue

            if choice_idx == 0:
                console.print("[dim]Загрузка отменена пользователем.[/dim]")
                console.print()
                continue

            if choice_idx == all_option:
                import time as _time

                results: list[dict] = []
                batch_started = _time.perf_counter()

                for entry in indexed:
                    label = entry.get("label") or ""
                    selected_url = entry.get("url") or ""
                    if not selected_url:
                        console.print(f"[yellow]Пропуск записи {label}: отсутствует URL.[/yellow]")
                        continue
                    translation_name = entry.get("translation_name")
                    quality_name = entry.get("quality")
                    console.print(f"[cyan]Скачиваем {label}[/cyan]")
                    if translation_name:
                        console.print(f"[dim]Перевод: {translation_name}[/dim]")
                    if quality_name:
                        console.print(f"[dim]Качество: {quality_name}[/dim]")
                    success, file_name, checksum_hex = _run_download_via_api(
                        selected_url, cookies_path=use_cookies
                    )
                    results.append(
                        {
                            "label": label,
                            "success": success,
                            "filename": file_name or "(нет файла)",
                            "translation": translation_name,
                            "quality": quality_name,
                            "checksum": checksum_hex,
                        }
                    )

                total_elapsed = _time.perf_counter() - batch_started
                console.print()
                console.print("[bold]Отчёт по пакетной загрузке:[/bold]")
                for r in results:
                    status = "[green]успешно[/green]" if r["success"] else "[red]ошибка[/red]"
                    translation_text = f", перевод: {r['translation']}" if r.get("translation") else ""
                    quality_text = f", качество: {r['quality']}" if r.get("quality") else ""
                    checksum_text = f", SHA-256: {r['checksum']}" if r.get("checksum") else ""
                    console.print(
                        f"- {r['label']}: {status}, файл: {r['filename']}"
                        f"{translation_text}{quality_text}{checksum_text}"
                    )
                console.print(f"[bold]Итого[/bold]: {_format_duration(total_elapsed)}")
                console.print()
                continue

            if not (1 <= choice_idx <= total_items):
                console.print("[red]Номер вне диапазона.[/red]")
                console.print()
                continue

            entry = indexed[choice_idx - 1]
            selected_url = entry.get("url") or ""
            if not selected_url:
                console.print("[yellow]URL отсутствует, загрузка пропущена.[/yellow]")
                console.print()
                continue
            translation_name = entry.get("translation_name")
            quality_name = entry.get("quality")
            if translation_name:
                console.print(f"[dim]Перевод: {translation_name}[/dim]")
            if quality_name:
                console.print(f"[dim]Качество: {quality_name}[/dim]")
            _run_download_via_api(selected_url, cookies_path=use_cookies)
            console.print()
            continue

        elif choice == 0:
            console.print("Выход.", style="dim")
            sys.exit(0)
        else:
            console.print("[red]Неизвестный пункт меню[/red]")
            console.print()


@click.command(
    name="grabvidzilla-api",
    help=(
        "\nCLI-клиент GrabVidZilla, работающий через HTTP API.\n\n"
        "Использование:\n"
        "  grabvidzilla-api                 # открыть меню\n\n"
        "Переменные окружения:\n"
        "  GVZ_API_URL — URL API сервера (по умолчанию: http://localhost:8000)\n\n"
        "Примеры:\n"
        "  GVZ_API_URL=http://api.example.com:8000 grabvidzilla-api\n"
        "\n"
    ),
)
@click.option(
    "--use-browser-parser/--no-browser-parser",
    "use_browser_parser",
    default=True,
    show_default=True,
    help="Автоматически задействовать Playwright, если статический парсер API ничего не нашёл.",
)
@click.option(
    "--browser-proxy",
    "browser_proxy_url",
    default=None,
    help="Прокси для браузерного парсера (пример: http://user:pass@host:3128).",
)
def main(use_browser_parser: bool, browser_proxy_url: Optional[str]) -> None:
    """
    Точка входа CLI API-клиента. Показывает интерактивное меню.
    """
    # Проверяем доступность API
    try:
        response = requests.get(f"{API_BASE_URL}/health", timeout=5)
        response.raise_for_status()
    except requests.exceptions.RequestException:
        console.print(f"[red]Ошибка: не удалось подключиться к API по адресу {API_BASE_URL}[/red]")
        console.print("[yellow]Убедитесь, что API сервер запущен.[/yellow]")
        console.print()
        if click.confirm("Продолжить всё равно?", default=False):
            pass
        else:
            sys.exit(1)

    _show_menu_and_handle(use_browser_parser=use_browser_parser, browser_proxy_url=browser_proxy_url)


if __name__ == "__main__":
    main()

