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
import threading
from typing import Optional, Tuple, Dict, List, Callable
from urllib.parse import urlparse
from collections import OrderedDict

import click
import requests
from rich.console import Console
from rich.live import Live
from rich.progress import Progress, BarColumn, TextColumn, SpinnerColumn
from rich.table import Table

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


def _track_single_task(
    task_id: str,
    session: requests.Session,
    api_url: str = API_BASE_URL,
    on_update: Optional[Callable[[dict], None]] = None,
    poll_interval: float = 0.5,
) -> dict:
    """
    Опрашивает API до завершения одной задачи (статус: completed, error, cancelled).

    Параметры:
        task_id: идентификатор задачи.
        session: HTTP-сессия для запросов.
        api_url: базовый URL API.
        on_update: колбек, вызывается с dict задачи при каждом успешном опросе.
        poll_interval: интервал между опросами в секундах.

    Возвращает финальный словарь состояния задачи.
    """
    terminal_states = {"completed", "error", "cancelled"}
    while True:
        try:
            resp = session.get(f"{api_url}/downloads/{task_id}", timeout=5)
            resp.raise_for_status()
            task = resp.json()
            if on_update:
                on_update(task)
            if task.get("status") in terminal_states:
                return task
        except requests.exceptions.RequestException:
            pass  # временные сбои сети — продолжаем опрос
        time.sleep(poll_interval)


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
            "source": "cli",  # Dashboard: отмечаем источник запроса
        }
        if cookies_path:
            payload["cookies_path"] = cookies_path
        if subtitle_lang:
            payload["subtitle_lang"] = subtitle_lang

        response = requests.post(f"{API_BASE_URL}/downloads", json=payload, timeout=10)
        response.raise_for_status()
        task_id = response.json()["id"]

        console.print(":rocket: [bold]Старт загрузки[/bold]", style="cyan")
        started_at = time.perf_counter()
        session = requests.Session()

        # Показываем прогресс-бар и опрашиваем задачу через _track_single_task
        with Progress(
            SpinnerColumn(),
            BarColumn(bar_width=40),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("{task.description}"),
            TextColumn("{task.fields[speed]}"),
            console=console,
            transient=True,
            refresh_per_second=4,
        ) as progress:
            progress_task = progress.add_task(
                "[cyan]Загрузка...[/cyan]",
                total=100,
                speed="—",
            )

            def on_update(task_status: dict) -> None:
                """Обновляем прогресс-бар при каждом опросе API."""
                pct = task_status.get("progress", 0.0) or 0.0
                speed = task_status.get("speed") or "—"
                progress.update(progress_task, completed=pct, speed=speed)

            # Блокирующий опрос до завершения задачи (0.5 сек между запросами)
            final = _track_single_task(
                task_id, session, api_url=API_BASE_URL,
                on_update=on_update, poll_interval=0.5,
            )

        state = final.get("status", "unknown")
        filename = final.get("filename")
        elapsed = time.perf_counter() - started_at

        if state == "completed":
            console.print()
            console.print(f":white_check_mark: [bold green]Готово[/bold green]: {filename}")
            console.print(f"[dim]Время скачивания: {_format_duration(elapsed)}[/dim]")
            file_size = final.get("file_size")
            if file_size:
                console.print(f"[dim]Размер файла: {_format_size(file_size)}[/dim]")
            checksum_hex = final.get("sha256")
            if checksum_hex:
                console.print(f"[dim]SHA-256: {checksum_hex}[/dim]")
            console.print()
            return True, filename, checksum_hex

        elif state == "error":
            console.print()
            err_text = final.get("error_message") or "Неизвестная ошибка"
            error_type = final.get("error_type")
            if error_type:
                err_text = f"[{error_type}] {err_text}"
            console.print(f":boom: [bold red]Ошибка[/bold red]: {err_text}")
            return False, None, None

        else:  # cancelled или unknown
            console.print()
            console.print("[yellow]Загрузка отменена[/yellow]")
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


def _extract_site(url: str) -> str:
    """Извлекает короткое имя сайта из URL (например, 'youtube', 'vk', 'tiktok')."""
    try:
        host = urlparse(url).netloc
        host = host.lstrip("www.")
        return host.split(".")[0]
    except Exception:
        return url[:12]


def _run_batch_download(
    urls: List[str],
    cookies_path: Optional[str] = None,
    fmt: Optional[str] = None,
    audio_only: bool = False,
    subtitle_lang: Optional[str] = None,
) -> None:
    """
    Batch-скачивание: отправляет все задачи и параллельно отслеживает их в живой таблице.

    Каждая задача отслеживается в отдельном потоке через _track_single_task.
    Живая таблица (rich.Live) обновляется каждые 0.5 сек до завершения всех задач.

    Параметры:
        urls: список URL для скачивания.
        cookies_path: путь к файлу cookies.
        fmt: строка формата yt-dlp.
        audio_only: если True — скачивать только аудио.
        subtitle_lang: код языка субтитров.
    """
    console.print(f"\n[bold]:clipboard: Batch download: {len(urls)} URLs[/bold]")
    console.print("─" * 50)

    session = requests.Session()

    # Шаг 1: отправляем все задачи и собираем task_id
    tasks: List[Dict] = []
    for i, url in enumerate(urls, 1):
        short_url = url[:57] + "..." if len(url) > 60 else url
        console.print(f"[{i}/{len(urls)}] Submitting: {short_url} ", end="")

        payload: Dict = {
            "url": url,
            "format": fmt if not audio_only else None,
            "audio_only": audio_only,
        }
        if cookies_path:
            payload["cookies_path"] = cookies_path
        if subtitle_lang:
            payload["subtitle_lang"] = subtitle_lang

        try:
            resp = session.post(f"{API_BASE_URL}/downloads", json=payload, timeout=10)
            resp.raise_for_status()
            task_id = resp.json()["id"]
            console.print(f"[green]:white_check_mark: {task_id[:8]}[/green]")
            tasks.append({
                "url": url, "task_id": task_id,
                "status": "queued", "progress": 0.0,
                "filename": None, "error": None, "speed": "",
            })
        except requests.exceptions.RequestException as e:
            err_msg = str(e)
            if hasattr(e, "response") and e.response is not None:
                try:
                    err_msg = e.response.json().get("detail", err_msg)
                except Exception:
                    pass
            console.print(f"[red]:x: Ошибка: {err_msg}[/red]")
            tasks.append({
                "url": url, "task_id": None,
                "status": "error", "progress": 0.0,
                "filename": None, "error": err_msg, "speed": "",
            })

    submitted = [t for t in tasks if t["task_id"] is not None]
    if not submitted:
        console.print("[red]Ни одна задача не была создана.[/red]")
        return

    console.print()

    # Шаг 2: параллельный трекинг — каждая задача в отдельном потоке через _track_single_task
    terminal_states = {"completed", "error", "cancelled"}

    def _track_task_thread(t: dict) -> None:
        """Отслеживает одну задачу в потоке, обновляя общий dict состояния."""
        def on_update(data: dict) -> None:
            t["status"] = data.get("status", "unknown")
            t["progress"] = data.get("progress") or 0.0
            t["filename"] = data.get("filename")
            # Скорость показываем только во время скачивания
            t["speed"] = data.get("speed") or "" if t["status"] == "downloading" else ""
            if t["status"] == "error":
                t["error"] = data.get("error_message", "Неизвестная ошибка")

        _track_single_task(t["task_id"], session, on_update=on_update, poll_interval=2.0)

    threads = [
        threading.Thread(target=_track_task_thread, args=(t,), daemon=True)
        for t in submitted
    ]
    for th in threads:
        th.start()

    def _make_table() -> Table:
        """Строит таблицу текущего состояния всех задач."""
        in_prog = sum(
            1 for t in tasks if t["task_id"] and t["status"] not in terminal_states
        )
        done = sum(1 for t in tasks if t["status"] == "completed")
        queued = sum(1 for t in tasks if t["status"] == "queued")
        failed = sum(1 for t in tasks if t["status"] == "error")

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("#", style="dim", width=10, no_wrap=True)
        table.add_column("Сайт", width=12, no_wrap=True)
        table.add_column("Статус", width=14, no_wrap=True)
        table.add_column("Прогресс", width=14, no_wrap=True)
        table.add_column("Скорость", width=12, no_wrap=True)
        table.add_column("Файл", width=22, no_wrap=True)

        for t in tasks:
            task_id_short = (t["task_id"] or "")[:8] or "—"
            site_str = _extract_site(t["url"])

            status = t["status"]
            if status == "completed":
                status_str = "[green]completed[/green]"
            elif status == "error":
                status_str = "[red]error[/red]"
            elif status == "cancelled":
                status_str = "[yellow]cancelled[/yellow]"
            elif status == "downloading":
                status_str = "[cyan]downloading[/cyan]"
            else:
                status_str = f"[dim]{status}[/dim]"

            pct = t.get("progress") or 0.0
            if t["task_id"] is None:
                progress_str = "[red]—[/red]"
            elif status == "completed":
                progress_str = "████ 100%"
            elif status in ("error", "cancelled"):
                progress_str = "—"
            else:
                filled = min(4, int(pct / 25))
                progress_str = "█" * filled + "░" * (4 - filled) + f" {pct:.0f}%"

            # Скорость — только во время активного скачивания
            speed_str = t.get("speed") or "" if status == "downloading" else ""

            filename = t.get("filename") or ""
            filename_short = ("…" + filename[-19:]) if len(filename) > 22 else filename

            table.add_row(
                task_id_short, site_str, status_str,
                progress_str, speed_str, filename_short,
            )

        return table

    # Живая таблица обновляется, пока хотя бы один поток активен
    with Live(_make_table(), console=console, refresh_per_second=2) as live:
        while any(th.is_alive() for th in threads):
            live.update(_make_table())
            time.sleep(0.5)
        live.update(_make_table())

    for th in threads:
        th.join()

    # Итоговая строка статистики — обычный print без разметки bold/big
    _in_prog = sum(1 for t in tasks if t["task_id"] and t["status"] not in terminal_states)
    _done = sum(1 for t in tasks if t["status"] == "completed")
    _queued = sum(1 for t in tasks if t["status"] == "queued")
    _failed = sum(1 for t in tasks if t["status"] == "error")
    console.print(
        f"⏳ In progress: {_in_prog}  "
        f"✅ Done: {_done}  "
        f"⏸ Queued: {_queued}  "
        f"❌ Failed: {_failed}"
    )

    # Шаг 3: итоговый отчёт
    done_count = sum(1 for t in tasks if t["status"] == "completed")
    failed_count = sum(1 for t in tasks if t["status"] == "error")
    cancelled_count = sum(1 for t in tasks if t["status"] == "cancelled")
    downloads_dir = os.getenv("DOWNLOADS_DIR", "Downloads")

    console.print()
    console.print("[bold]" + "\u2501" * 40 + "[/bold]")
    console.print(":bar_chart: [bold]Результат batch-скачивания:[/bold]")
    console.print(f":white_check_mark: Успешно:  {done_count}")
    console.print(f":x: Ошибок:   {failed_count}")
    if cancelled_count:
        console.print(f":no_entry: Отменено: {cancelled_count}")
    console.print(f":open_file_folder: Файлы сохранены в: {downloads_dir}")
    console.print("[bold]" + "\u2501" * 40 + "[/bold]")


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
        console.print("1. Скачать видео (один URL, несколько или файл со списком)")
        console.print("2. help")
        console.print("3. Загрузить cookies")
        console.print("4. Найти видео на странице")
        console.print("5. Поиск видео на специфичных сайтах")
        console.print("0. Выход")
        choice = click.prompt("Выберите пункт", type=int, default=1)

        if choice == 1:
            # Подсказка: принимаем один URL, несколько через пробел или файл со списком
            console.print()
            console.print("[bold]Введите URL для скачивания.[/bold]")
            console.print(
                "[dim]Можно: одну ссылку / несколько через пробел"
                " / путь к файлу со списком URL[/dim]"
            )
            raw = click.prompt("URL(ы) или файл", type=str).strip()

            if not raw:
                console.print("[yellow]URL не введён.[/yellow]")
                console.print()
                continue

            # Разбираем ввод: если это файл — читаем из него, иначе делим по пробелам
            if os.path.isfile(raw):
                with open(raw, "r", encoding="utf-8") as f:
                    input_urls = [
                        ln.strip() for ln in f
                        if ln.strip() and not ln.strip().startswith("#")
                    ]
            else:
                input_urls = raw.split()

            if not input_urls:
                console.print("[red]:x: Не найдено ни одного URL.[/red]")
                console.print()
                continue

            # Cookies по умолчанию из tools/cookies.txt
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            default_cookies = os.path.join(project_root, "tools", "cookies.txt")
            use_cookies = default_cookies if os.path.isfile(default_cookies) else None

            if len(input_urls) > 1:
                # Batch-режим: несколько URL — параллельный трекинг через _run_batch_download
                console.print(
                    f"[dim]Найдено {len(input_urls)} URL — запускаем batch-режим.[/dim]"
                )
                try:
                    _run_batch_download(urls=input_urls, cookies_path=use_cookies)
                except Exception as exc:
                    console.print(f":boom: [bold red]Ошибка[/bold red]: {exc}")
                console.print()
                continue

            # Одиночный режим — анализ, выбор качества, скачивание
            url = input_urls[0]
            try:
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
            console.print(":inbox_tray: [bold]Скачивание нескольких видео (batch-режим):[/bold]")
            console.print("─" * 50)
            console.print("Пункт 1 принимает три формата ввода:")
            console.print()
            console.print("  [cyan]• Одна ссылка:[/cyan]")
            console.print("      https://youtube.com/watch?v=xxx")
            console.print()
            console.print("  [cyan]• Несколько ссылок через пробел:[/cyan]")
            console.print("      https://youtube.com/... https://vk.com/...")
            console.print()
            console.print("  [cyan]• Путь к текстовому файлу (один URL на строку):[/cyan]")
            console.print("      /home/user/urls.txt   или   urls.txt")
            console.print()
            console.print("[dim]Формат файла urls.txt:[/dim]")
            console.print("[dim]  # это комментарий — строка игнорируется[/dim]")
            console.print("[dim]  https://youtube.com/watch?v=aaa[/dim]")
            console.print("[dim]  https://tiktok.com/@user/video/bbb[/dim]")
            console.print()
            console.print("[dim]Прогресс batch — живая таблица, итог по завершении всех задач.[/dim]")
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


@click.group(
    name="grabvidzilla-api",
    invoke_without_command=True,
    help=(
        "\nCLI-клиент GrabVidZilla, работающий через HTTP API.\n\n"
        "Использование:\n"
        "  grabvidzilla-api                        # открыть интерактивное меню\n"
        "  grabvidzilla-api download URL           # скачать одно видео\n"
        "  grabvidzilla-api download URL1 URL2     # batch-скачивание\n"
        "  grabvidzilla-api download --batch file  # batch из файла\n\n"
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
@click.pass_context
def main(ctx: click.Context, use_browser_parser: bool, browser_proxy_url: Optional[str]) -> None:
    """
    Точка входа CLI API-клиента. Без подкоманды — открывает интерактивное меню.
    """
    # Проверяем доступность API перед любой командой, кроме --help
    if "--help" not in sys.argv and "-h" not in sys.argv:
        try:
            response = requests.get(f"{API_BASE_URL}/health", timeout=5)
            response.raise_for_status()
        except requests.exceptions.RequestException:
            console.print(
                f"[red]Ошибка: не удалось подключиться к API по адресу {API_BASE_URL}[/red]"
            )
            console.print("[yellow]Убедитесь, что API сервер запущен.[/yellow]")
            console.print()
            if click.confirm("Продолжить всё равно?", default=False):
                pass
            else:
                sys.exit(1)

    # Если подкоманда не указана — показываем интерактивное меню (старое поведение)
    if ctx.invoked_subcommand is None:
        _show_menu_and_handle(use_browser_parser=use_browser_parser, browser_proxy_url=browser_proxy_url)


@main.command(
    name="download",
    help=(
        "Скачать одно или несколько видео через API.\n\n"
        "Примеры:\n"
        "  grabvidzilla-api download https://youtube.com/...\n"
        "  grabvidzilla-api download URL1 URL2 URL3\n"
        "  grabvidzilla-api download --batch urls.txt\n"
        "  grabvidzilla-api download URL --quality 720p\n"
        "  grabvidzilla-api download URL --audio-only\n"
    ),
)
@click.argument("urls", nargs=-1, required=False)
@click.option(
    "--batch", "-b",
    "batch_file",
    type=click.Path(exists=True),
    default=None,
    help="Текстовый файл со списком URL (по одному на строку, # — комментарий).",
)
@click.option(
    "--quality", "-q",
    "quality",
    default=None,
    help="Качество: 720p, 1080p, best и т.д. (по умолчанию: best).",
)
@click.option(
    "--audio-only", "-a",
    "audio_only",
    is_flag=True,
    default=False,
    help="Скачивать только аудио.",
)
@click.option(
    "--cookies", "-c",
    "cookies_path",
    type=click.Path(),
    default=None,
    help="Путь к файлу cookies.txt (Netscape формат).",
)
@click.option(
    "--subtitle-lang", "-s",
    "subtitle_lang",
    default=None,
    help="Код языка субтитров (например: ru, en).",
)
def download_cmd(
    urls: tuple,
    batch_file: Optional[str],
    quality: Optional[str],
    audio_only: bool,
    cookies_path: Optional[str],
    subtitle_lang: Optional[str],
) -> None:
    """
    Скачивает одно или несколько видео через REST API.

    При одном URL — одиночный режим с прогресс-баром.
    При нескольких URL или --batch — batch-режим с живой таблицей прогресса.
    Все опции (--quality, --audio-only и т.д.) применяются ко всем URL.
    """
    # Собираем URL из аргументов командной строки и файла --batch
    all_urls = list(urls)
    if batch_file:
        with open(batch_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    all_urls.append(line)

    if not all_urls:
        console.print("[red]:x: Укажите хотя бы один URL или файл --batch[/red]")
        raise SystemExit(1)

    # Определяем формат на основе указанного качества
    if audio_only:
        fmt = None  # _run_download_via_api сам передаёт audio_only=True в API
    elif quality:
        fmt = _build_format_selector(quality)
    else:
        fmt = None  # API выберет лучшее качество автоматически

    # Если cookies не указаны явно — пробуем tools/cookies.txt
    if not cookies_path:
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        default_cookies = os.path.join(project_root, "tools", "cookies.txt")
        if os.path.isfile(default_cookies):
            cookies_path = default_cookies

    if len(all_urls) == 1:
        # Одиночный режим — стандартный прогресс-бар, поведение как раньше
        _run_download_via_api(
            url=all_urls[0],
            cookies_path=cookies_path,
            fmt=fmt,
            audio_only=audio_only,
            subtitle_lang=subtitle_lang,
        )
    else:
        # Batch-режим — живая таблица с параллельным трекингом всех задач
        _run_batch_download(
            urls=all_urls,
            cookies_path=cookies_path,
            fmt=fmt,
            audio_only=audio_only,
            subtitle_lang=subtitle_lang,
        )


if __name__ == "__main__":
    main()

