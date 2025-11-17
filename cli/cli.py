"""
Модуль командной строки для GrabVidZilla.

Использует click для команд и rich для красивого вывода.
"""

from __future__ import annotations

import sys
from typing import Optional

import click
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, SpinnerColumn

from core.downloader import download_video, analyze_video
from core.parser import find_media_urls


console = Console()


def _run_download(
    url: str,
    output_path: str,
    cookies_path: Optional[str] = None,
    fmt: Optional[str] = None,
) -> tuple[bool, Optional[str], float, Optional[int]]:
    """
    Выполняет загрузку одного URL, показывая прогресс через rich.Progress.

    Returns:
        Кортеж (success, file_path, elapsed_s, size_bytes):
        - success: True при успешной загрузке, False при ошибке;
        - file_path: полный путь к загруженному файлу (если известен);
        - elapsed_s: примерное время скачивания в секундах;
        - size_bytes: размер файла в байтах (если удалось определить).
    """
    import os
    import re
    import time

    def _format_speed(bps: Optional[float]) -> str:
        if not bps or bps <= 0:
            return "—"
        units = ["Б/с", "КБ/с", "МБ/с", "ГБ/с"]
        i = 0
        v = float(bps)
        while v >= 1024.0 and i < len(units) - 1:
            v /= 1024.0
            i += 1
        return f"{v:.1f} {units[i]}"

    # Создаём прогресс-бар с общей шкалой 0..100 (проценты сообщает core)
    with Progress(
        SpinnerColumn(),
        BarColumn(bar_width=40),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.description}"),
        TextColumn("{task.fields[speed]}"),
        console=console,
        transient=True,  # скрыть прогресс после завершения
    ) as progress:
        task_id = progress.add_task("[cyan]Загрузка...", total=100, speed="—")

        def on_progress(percent: float) -> None:
            # Обновляем прогресс (percent уже в диапазоне 0..100)
            progress.update(task_id, completed=percent)

        def on_progress_info(info: dict) -> None:
            # Обновляем поле скорости в прогрессе
            progress.update(task_id, speed=_format_speed(info.get("speed")))

        console.print(":rocket: [bold]Старт загрузки[/bold]", style="cyan")
        started_at = time.perf_counter()
        size_bytes: Optional[int] = None
        file_path_str: Optional[str] = None
        try:
            file_path = download_video(
                url=url,
                output_path=output_path,
                progress_callback=on_progress,
                progress_info_callback=on_progress_info,
                cookies_path=cookies_path,
                format=fmt,
            )
            # Убедимся, что шкала заполнена
            progress.update(task_id, completed=100)
            elapsed = time.perf_counter() - started_at
            # Переименуем файл в «чистое» имя: убираем [id] и части после вертикальной черты/эмодзи
            original_dir = os.path.dirname(file_path)
            original_name = os.path.basename(file_path)
            stem, ext = os.path.splitext(original_name)
            stem = re.split(r"[|｜]", stem)[0].strip()
            stem = re.sub(r"\s*\[[A-Za-z0-9_-]{8,}\]\s*$", "", stem).strip()
            stem = stem.replace("🎖️", "").strip()
            cleaned_name = f"{stem}{ext}"
            cleaned_path = os.path.join(original_dir, cleaned_name)
            if cleaned_path != file_path:
                candidate_path = cleaned_path
                suffix = 1
                while os.path.exists(candidate_path):
                    candidate_path = os.path.join(original_dir, f"{stem} ({suffix}){ext}")
                    suffix += 1
                os.replace(file_path, candidate_path)
                file_path = candidate_path

            # Форматируем длительность: секунды или минуты и секунды
            def _format_duration(seconds: float) -> str:
                if seconds >= 60.0:
                    m = int(seconds // 60)
                    s = seconds - (m * 60)
                    if s >= 10:
                        return f"{m} мин {int(s)} сек"
                    return f"{m} мин {s:.1f} сек"
                return f"{seconds:.1f} сек"

            console.print(f":white_check_mark: [bold green]Готово[/bold green]: {os.path.basename(file_path)}")
            console.print(f"[dim]Время скачивания: {_format_duration(elapsed)}[/dim]")

            # Размер итогового файла (в МБ/ГБ)
            def _format_size(num_bytes: int) -> str:
                units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
                i = 0
                v = float(num_bytes)
                while v >= 1024.0 and i < len(units) - 1:
                    v /= 1024.0
                    i += 1
                # Для МБ/ГБ показываем одну цифру после запятой
                if units[i] in ("МБ", "ГБ", "ТБ"):
                    return f"{v:.1f} {units[i]}"
                return f"{int(v)} {units[i]}"

            try:
                size_bytes = os.path.getsize(file_path)
                console.print(f"[dim]Размер файла: {_format_size(size_bytes)}[/dim]")
            except Exception:
                size_bytes = None
            console.print()  # отступ после успешного завершения
            file_path_str = file_path
            return True, file_path_str, float(elapsed), size_bytes
        except Exception as exc:
            # Обрабатываем исключения из core и выводим дружелюбно
            console.print(f":boom: [bold red]Ошибка[/bold red]: {exc}")
            # Не пробрасываем исключение выше, чтобы не было Aborted! и оставаться в меню
            return False, None, 0.0, None
        finally:
            console.print()  # общий отступ после операции (успех/ошибка)


def _show_menu_and_handle() -> None:
    """
    Простое интерактивное меню, если команда запущена без аргументов.
    """
    while True:
        console.print()  # отступ перед показом меню
        console.print("[bold]GrabVidZilla[/bold] — кроссплатформенный загрузчик видео")
        console.print("1. Скачать видео")
        console.print("2. help")
        console.print("3. Загрузить cookies")
        console.print("4. Найти видео на странице")
        console.print("0. Выход")
        choice = click.prompt("Выберите пункт", type=int, default=1)

        if choice == 1:
            url = click.prompt("Введите URL видео", type=str)
            # Сохраняем по умолчанию в папку Downloads в корне проекта
            try:
                # Если есть cookies в tools/cookies.txt — используем их
                import os
                project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
                default_cookies = os.path.join(project_root, "tools", "cookies.txt")
                use_cookies = default_cookies if os.path.isfile(default_cookies) else None

                # Анализируем и предлагаем выбрать качество
                try:
                    info, qualities, _subtitle_langs = analyze_video(url, cookies_path=use_cookies)
                    if not qualities:
                        qualities = ["best"]
                except Exception as e:
                    console.print(f"[yellow]Не удалось выполнить анализ ({e}).[/yellow]")
                    # Предложим загрузить/указать cookies и попробовать снова
                    try_again = click.confirm("Использовать cookies (tools/cookies.txt) или указать путь и попробовать снова?", default=True)
                    if try_again:
                        import os
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
                            info, qualities, _subtitle_langs = analyze_video(url, cookies_path=use_cookies)
                            if not qualities:
                                qualities = ["best"]
                        except Exception as e2:
                            console.print(f"[yellow]Повторный анализ не удался ({e2}). Будет использовано качество по умолчанию.[/yellow]")
                            qualities = ["best"]
                    else:
                        qualities = ["best"]

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

                # Построение строки формата для yt-dlp
                def _build_format_selector(selected_quality: str) -> str:
                    if selected_quality == "audio only":
                        return "bestaudio/best"
                    try:
                        if selected_quality.endswith("p"):
                            h = int(selected_quality[:-1])
                            return f"bv*[height<={h}]+ba/best[height<={h}]"
                    except Exception:
                        pass
                    return "bv*+ba/best"

                fmt = _build_format_selector(selected_quality)

                _run_download(url=url, output_path="Downloads", cookies_path=use_cookies, fmt=fmt)
            except Exception:
                # Ошибка уже выведена внутри _run_download; возвращаемся в меню
                pass
            finally:
                console.print()  # отступ перед возвратом к меню
        elif choice == 2:
            # Показать help основной команды
            ctx = click.get_current_context(silent=True)
            if ctx is None:
                # Создадим временный контекст, если нет активного
                with click.Context(main) as temp_ctx:
                    console.print()  # отступ сверху
                    console.print(temp_ctx.get_help())
                    console.print()  # отступ снизу
            else:
                console.print()  # отступ сверху
                console.print(ctx.get_help())
                console.print()  # отступ снизу
            console.print("[cyan]0. Вернуться в меню[/cyan]")
            _ = click.prompt("Нажмите 0 для возврата", type=int, default=0)
            console.print()  # отступ перед возвратом к меню
            continue
        elif choice == 3:
            # Загрузка/обновление cookies в tools/cookies.txt c объединением записей.
            import os
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
            # Поиск медиаконтента на HTML-странице и последующая загрузка выбранного видео
            import os

            page_url = click.prompt("Введите URL страницы", type=str)

            # Определяем cookies так же, как в режиме скачивания видео
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            default_cookies = os.path.join(project_root, "tools", "cookies.txt")
            use_cookies = default_cookies if os.path.isfile(default_cookies) else None

            try:
                hls_urls, file_urls = find_media_urls(page_url, cookies_path=use_cookies)
            except Exception as exc:
                console.print(f"[red]Не удалось найти видео на странице[/red]: {exc}")
                console.print()
                continue

            if not hls_urls and not file_urls:
                console.print("[yellow]На странице не найдено ни одного видео или HLS-потока.[/yellow]")
                console.print()
                continue

            # Формируем единый нумерованный список для выбора
            console.print("[bold]Найденные видео и потоки:[/bold]")
            indexed: list[tuple[str, str]] = []  # (label, url)

            if hls_urls:
                console.print("HLS (m3u8):")
                for idx, u in enumerate(hls_urls, start=1):
                    label = f"HLS #{idx}"
                    console.print(f"  {len(indexed)+1}. {label}: {u}")
                    indexed.append((label, u))

            if file_urls:
                console.print("Файлы:")
                for idx, u in enumerate(file_urls, start=1):
                    label = f"FILE #{idx}"
                    console.print(f"  {len(indexed)+1}. {label}: {u}")
                    indexed.append((label, u))

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
                # Скачиваем все найденные ссылки по очереди с небольшим отчётом в конце
                import os
                import time

                results: list[dict] = []
                batch_started = time.perf_counter()

                for label, selected_url in indexed:
                    console.print(f"[cyan]Скачиваем {label}[/cyan]")
                    success, file_path, elapsed_s, size_bytes = _run_download(
                        url=selected_url,
                        output_path="Downloads",
                        cookies_path=use_cookies,
                        fmt=None,
                    )
                    filename = os.path.basename(file_path) if file_path else "(нет файла)"
                    results.append(
                        {
                            "label": label,
                            "url": selected_url,
                            "success": success,
                            "filename": filename,
                            "elapsed_s": elapsed_s,
                            "size_bytes": size_bytes,
                        }
                    )

                total_elapsed = time.perf_counter() - batch_started
                total_bytes = sum(r["size_bytes"] or 0 for r in results)

                def _fmt_size(num_bytes: int) -> str:
                    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
                    i = 0
                    v = float(num_bytes)
                    while v >= 1024.0 and i < len(units) - 1:
                        v /= 1024.0
                        i += 1
                    if units[i] in ("МБ", "ГБ", "ТБ"):
                        return f"{v:.1f} {units[i]}"
                    return f"{int(v)} {units[i]}"

                def _fmt_duration(seconds: float) -> str:
                    if seconds >= 60.0:
                        m = int(seconds // 60)
                        s = seconds - (m * 60)
                        if s >= 10:
                            return f"{m} мин {int(s)} сек"
                        return f"{m} мин {s:.1f} сек"
                    return f"{seconds:.1f} сек"

                console.print()
                console.print("[bold]Отчёт по пакетной загрузке:[/bold]")
                for r in results:
                    status = "[green]успешно[/green]" if r["success"] else "[red]ошибка[/red]"
                    size_text = (
                        _fmt_size(r["size_bytes"]) if r["size_bytes"] is not None else "n/a"
                    )
                    console.print(
                        f"- {r['label']}: {status}, файл: {r['filename']}, "
                        f"время: {_fmt_duration(r['elapsed_s'])}, размер: {size_text}"
                    )

                console.print(
                    f"[bold]Итого[/bold]: {_fmt_duration(total_elapsed)}, "
                    f"суммарный размер: {_fmt_size(total_bytes)}"
                )
                console.print()
                continue

            if not (1 <= choice_idx <= total_items):
                console.print("[red]Номер вне диапазона.[/red]")
                console.print()
                continue

            _, selected_url = indexed[choice_idx - 1]
            # Для найденных ссылок не навязываем формат — пусть ядро само решит
            _run_download(
                url=selected_url,
                output_path="Downloads",
                cookies_path=use_cookies,
                fmt=None,
            )
            console.print()
            continue
        elif choice == 0:
            console.print("Выход.", style="dim")
            sys.exit(0)
        else:
            console.print("[red]Неизвестный пункт меню[/red]")
            console.print()  # отступ перед повторным показом меню


@click.command(
    name="grabvidzilla",
    help=(
        "\nЗагрузчик видео по URL с прогресс-баром.\n\n"
        "Использование:\n"
        "  grabvidzilla URL [-o PATH] [--cookies FILE]   # скачать по URL\n"
        "  grabvidzilla                 # открыть простое меню\n\n"
        "Пояснения:\n"
        "  URL — необязателен; без URL откроется меню\n"
        "  [-о, --output PATH] — каталог для сохранения (CLI-режим)\n"
        "  [--cookies FILE] — путь к cookies.txt (Netscape). Если не указан, берётся tools/cookies.txt (если есть)\n\n"
        "Сохранение:\n"
        "  CLI: по умолчанию в текущую папку (или укажите -о)\n"
        "  Меню: по умолчанию в папку 'Downloads' в корне проекта\n\n"
        "Примеры:\n"
        "  grabvidzilla \"https://youtu.be/...\" -о \"./downloads\"\n"
        "  grabvidzilla \"https://vkvideo.ru/...\" --cookies tools/cookies.txt\n"
        "\n"
    ),
)
@click.argument("url", required=False)
@click.option(
    "--output",
    "-o",
    "output_path",
    default="Downloads",
    show_default=True,
    help="Каталог для сохранения файлов (CLI-режим). По умолчанию — 'Downloads' в корне проекта.",
    type=click.Path(file_okay=False, dir_okay=True, writable=True, path_type=str),
)
@click.option(
    "--cookies",
    "cookies_path",
    default=None,
    help="Путь к cookies.txt (Netscape формат). Если не задан, используется tools/cookies.txt при наличии.",
    type=click.Path(file_okay=True, dir_okay=False, writable=False, path_type=str),
)
def main(url: Optional[str], output_path: str, cookies_path: Optional[str]) -> None:
    """
    Точка входа CLI приложения. Если URL указан — запускаем загрузку напрямую,
    иначе показываем простое меню с вариантами.
    """
    if url:
        # Если cookies не указаны явно — попробуем tools/cookies.txt
        if not cookies_path:
            import os
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            default_cookies = os.path.join(project_root, "tools", "cookies.txt")
            cookies_path = default_cookies if os.path.isfile(default_cookies) else None
        # Прямой режим по URL без интерактивного выбора качества — используем формат по умолчанию
        _run_download(url=url, output_path=output_path, cookies_path=cookies_path, fmt=None)
    else:
        _show_menu_and_handle()


if __name__ == "__main__":
    main()

