"""
CLI-клиент для GrabVidZilla, работающий через HTTP API.

Использует click для команд и rich для красивого вывода.
Все операции выполняются через REST API (FastAPI).
"""

from __future__ import annotations

import os
import sys
import time
from typing import Optional

import click
import requests
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, SpinnerColumn

# URL API по умолчанию
API_BASE_URL = os.getenv("GVZ_API_URL", "http://localhost:8000")

console = Console()


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
) -> tuple[bool, Optional[str]]:
    """
    Выполняет загрузку через API, показывая прогресс через rich.Progress.

    Returns:
        Кортеж (success, filename):
        - success: True при успешной загрузке, False при ошибке;
        - filename: имя загруженного файла (если известен).
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

                    state = task_status.get("state", "unknown")
                    progress_percent = task_status.get("progress_percent", 0.0)
                    speed_bps = task_status.get("speed_bps")
                    filename = task_status.get("filename")
                    error = task_status.get("error")
                    bytes_downloaded = task_status.get("bytes_downloaded")
                    total_bytes = task_status.get("total_bytes")

                    # Обновляем прогресс при каждом опросе для плавного отображения
                    speed_str = _format_speed(speed_bps)
                    # Обновляем все поля за один раз (без состояния в описании)
                    progress.update(
                        task_id_progress,
                        completed=progress_percent,
                        description="[cyan]Загрузка...[/cyan]",
                        speed=speed_str,
                    )
                    last_progress = progress_percent

                    if state == "completed":
                        progress.update(task_id_progress, completed=100, speed=_format_speed(speed_bps))
                        elapsed = time.perf_counter() - started_at
                        console.print()  # Новая строка после прогресс-бара
                        console.print(f":white_check_mark: [bold green]Готово[/bold green]: {filename}")
                        console.print(f"[dim]Время скачивания: {_format_duration(elapsed)}[/dim]")
                        
                        # Размер файла (используем total_bytes или bytes_downloaded из статуса)
                        size_bytes = total_bytes or bytes_downloaded
                        if size_bytes:
                            console.print(f"[dim]Размер файла: {_format_size(size_bytes)}[/dim]")
                        console.print()  # отступ после успешного завершения
                        return True, filename

                    elif state == "failed":
                        console.print()  # Новая строка после прогресс-бара
                        console.print(f":boom: [bold red]Ошибка[/bold red]: {error or 'Неизвестная ошибка'}")
                        return False, None

                    elif state == "cancelled":
                        console.print()  # Новая строка после прогресс-бара
                        console.print("[yellow]Загрузка отменена[/yellow]")
                        return False, None

                    # Ждём перед следующим опросом (опрашиваем чаще для плавного обновления)
                    time.sleep(0.5)

                except requests.exceptions.RequestException as e:
                    console.print(f"[red]Ошибка получения статуса: {e}[/red]")
                    return False, None

    except requests.exceptions.RequestException as e:
        error_msg = str(e)
        if hasattr(e, "response") and e.response is not None:
            try:
                detail = e.response.json().get("detail", error_msg)
                error_msg = detail
            except Exception:
                error_msg = f"HTTP {e.response.status_code}: {error_msg}"
        console.print(f":boom: [bold red]Ошибка[/bold red]: {error_msg}")
        return False, None
    except Exception as exc:
        console.print(f":boom: [bold red]Ошибка[/bold red]: {exc}")
        return False, None


def _show_menu_and_handle() -> None:
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
            except Exception:
                # Ошибка уже выведена внутри _run_download_via_api; возвращаемся в меню
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
            # Поиск медиаконтента на HTML-странице через API
            # Примечание: этот функционал требует прямого доступа к core.parser,
            # поэтому в API-режиме он может быть недоступен.
            # Можно либо добавить эндпоинт в API, либо оставить как есть.
            console.print("[yellow]Поиск видео на странице через API пока не реализован.[/yellow]")
            console.print("[dim]Используйте обычный CLI (python -m cli.cli) для этой функции.[/dim]")
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
def main() -> None:
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

    _show_menu_and_handle()


if __name__ == "__main__":
    main()

