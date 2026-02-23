"""
Модуль командной строки для GrabVidZilla.

Использует click для команд и rich для красивого вывода.
"""

from __future__ import annotations

import sys
import re
from typing import Optional, Tuple, Dict, List
from urllib.parse import urlparse

import click
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, SpinnerColumn
from collections import OrderedDict

from core.downloader import download_video, analyze_video, compute_file_checksum
from core.parser import find_media_urls, fetch_media_urls_with_browser
from core.site_parsers import get_adapter_for_url


console = Console()


def _parse_proxy_url(proxy_url: Optional[str]) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """
    Разбирает строку прокси в структуру Playwright.
    Возвращает (proxy_dict, error_message).
    """
    if not proxy_url:
        return None, None

    try:
        parsed = urlparse(proxy_url)
    except Exception:
        return None, "Некорректный формат прокси URL."

    if not parsed.scheme or not parsed.hostname:
        return None, "Прокси URL должен содержать схему и хост."

    server = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        server += f":{parsed.port}"

    proxy_dict: Dict[str, str] = {"server": server}
    if parsed.username:
        proxy_dict["username"] = parsed.username
    if parsed.password:
        proxy_dict["password"] = parsed.password

    return proxy_dict, None


def _run_download(
    url: str,
    output_path: str,
    cookies_path: Optional[str] = None,
    fmt: Optional[str] = None,
    desired_basename: Optional[str] = None,
    cookies_from_browser: Optional[str] = None,
) -> tuple[bool, Optional[str], float, Optional[int], Optional[str]]:
    """
    Выполняет загрузку одного URL, показывая прогресс через rich.Progress.

    Returns:
        Кортеж (success, file_path, elapsed_s, size_bytes, checksum_hex):
        - success: True при успешной загрузке, False при ошибке;
        - file_path: полный путь к загруженному файлу (если известен);
        - elapsed_s: примерное время скачивания в секундах;
        - size_bytes: размер файла в байтах (если удалось определить);
        - checksum_hex: контрольная сумма SHA-256 (если удалось посчитать).
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
    checksum_hex: Optional[str] = None

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
                cookies_from_browser=cookies_from_browser,
            )
            # Убедимся, что шкала заполнена
            progress.update(task_id, completed=100)
            elapsed = time.perf_counter() - started_at
            # Переименуем файл в «чистое» имя: убираем [id] и части после вертикальной черты/эмодзи
            original_dir = os.path.dirname(file_path)
            original_name = os.path.basename(file_path)
            stem, ext = os.path.splitext(original_name)

            def _sanitize_component(raw: str) -> str:
                cleaned = raw.replace("：", " ")
                cleaned = re.sub(r"[\\/:*?\"<>|]", " ", cleaned)
                cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
                return cleaned[:150]

            preferred_stem = _sanitize_component(desired_basename) if desired_basename else ""
            if preferred_stem:
                base_stem = preferred_stem
            else:
                stem = re.split(r"[|｜]", stem)[0].strip()
                stem = re.sub(r"\s*\[[A-Za-z0-9_-]{8,}\]\s*$", "", stem).strip()
                stem = stem.replace("🎖️", "").strip()
                base_stem = _sanitize_component(stem)

            if not base_stem:
                base_stem = "video"

            if not ext:
                ext = ".mp4"

            cleaned_name = f"{base_stem}{ext}"
            cleaned_path = os.path.join(original_dir, cleaned_name)
            if cleaned_path != file_path:
                candidate_path = cleaned_path
                suffix = 1
                while os.path.exists(candidate_path):
                    candidate_path = os.path.join(original_dir, f"{base_stem} ({suffix}){ext}")
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
            try:
                checksum_hex = compute_file_checksum(file_path)
                console.print(f"[dim]SHA-256: {checksum_hex}[/dim]")
            except Exception:
                checksum_hex = None

            file_path_str = file_path
            return True, file_path_str, float(elapsed), size_bytes, checksum_hex
        except Exception as exc:
            # Обрабатываем исключения из core и выводим дружелюбно
            console.print(f":boom: [bold red]Ошибка[/bold red]: {exc}")
            # Не пробрасываем исключение выше, чтобы не было Aborted! и оставаться в меню
            return False, None, 0.0, None, None
        finally:
            console.print()  # общий отступ после операции (успех/ошибка)


def _detect_browsers() -> List[str]:
    """Возвращает список браузеров, которые можно использовать для cookies."""
    # yt-dlp поддерживает: chrome, firefox, opera, edge, brave, vivaldi, safari
    return ["chrome", "firefox", "edge", "brave", "opera", "vivaldi"]


def _is_bot_detection_error(error_text: str) -> bool:
    """Проверяет, является ли ошибка результатом бот-детекции YouTube."""
    lower = error_text.lower()
    return "sign in to confirm" in lower or "not a bot" in lower


def _show_menu_and_handle(
    use_browser_parser: bool,
    browser_proxy_url: Optional[str],
) -> None:
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
        console.print("5. Поиск видео на специфичных сайтах")
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
                video_title: Optional[str] = None

                # Анализируем и предлагаем выбрать качество
                use_browser_cookies: Optional[str] = None
                try:
                    info, qualities, _subtitle_langs = analyze_video(url, cookies_path=use_cookies)
                    if not qualities:
                        qualities = ["best"]
                    video_title = info.get("title")
                except Exception as e:
                    error_text = str(e)
                    console.print(f"[yellow]Не удалось выполнить анализ ({e}).[/yellow]")

                    # Если YouTube требует авторизацию — предлагаем cookies из браузера
                    if _is_bot_detection_error(error_text):
                        console.print()
                        console.print("[bold yellow]YouTube требует подтверждение, что вы не бот.[/bold yellow]")
                        console.print("[cyan]Можно извлечь cookies прямо из вашего браузера (вы должны быть залогинены в YouTube).[/cyan]")
                        browsers = _detect_browsers()
                        console.print("[bold]Выберите браузер для извлечения cookies:[/bold]")
                        for idx, b in enumerate(browsers, start=1):
                            console.print(f"  {idx}. {b}")
                        console.print("  0. Пропустить (использовать качество по умолчанию)")
                        try:
                            browser_choice = click.prompt("Номер браузера", type=int, default=1)
                        except Exception:
                            browser_choice = 0
                        if 1 <= browser_choice <= len(browsers):
                            use_browser_cookies = browsers[browser_choice - 1]
                            console.print(f"[cyan]Извлекаем cookies из {use_browser_cookies}...[/cyan]")
                            try:
                                info, qualities, _subtitle_langs = analyze_video(
                                    url, cookies_path=use_cookies,
                                    cookies_from_browser=use_browser_cookies,
                                )
                                if not qualities:
                                    qualities = ["best"]
                                video_title = info.get("title")
                                console.print(f"[green]Анализ успешен с cookies из {use_browser_cookies}![/green]")
                            except Exception as e3:
                                console.print(f"[yellow]Не удалось с cookies из браузера ({e3}). Будет использовано качество по умолчанию.[/yellow]")
                                qualities = ["best"]
                        else:
                            qualities = ["best"]
                    else:
                        # Обычная ошибка — предложим cookies-файл
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
                                video_title = info.get("title")
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

                _run_download(
                    url=url,
                    output_path="Downloads",
                    cookies_path=use_cookies,
                    fmt=fmt,
                    desired_basename=video_title,
                    cookies_from_browser=use_browser_cookies,
                )
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

            page_title: Optional[str] = None
            translations: Optional[List[Dict[str, str]]] = None
            hls_entries: List[Dict[str, str]] = []
            selected_translation_hash: Optional[str] = None
            selected_translation_name: Optional[str] = None

            try:
                hls_urls, file_urls, page_title, translations, hls_entries = find_media_urls(
                    page_url, cookies_path=use_cookies
                )
            except Exception as exc:
                console.print(f"[red]Не удалось найти видео на странице[/red]: {exc}")
                console.print()
                continue

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
                    selected = translations[translation_choice - 1]
                    selected_translation_hash = selected.get("hash")
                    selected_translation_name = selected.get("name")
                    try:
                        hls_urls, file_urls, page_title, _, hls_entries = find_media_urls(
                            page_url,
                            cookies_path=use_cookies,
                            translation_hash=selected_translation_hash,
                        )
                    except Exception as exc:
                        console.print(f"[red]Не удалось получить ссылки для выбранного перевода[/red]: {exc}")
                        console.print()
                        continue
                else:
                    console.print("[red]Номер перевода вне диапазона.[/red]")
                    console.print()
                    continue

            used_browser = False
            if not hls_urls and not file_urls and use_browser_parser:
                console.print("[cyan]Статический парсер ничего не нашёл. Пробуем браузерный режим (Playwright)...[/cyan]")
                proxy_dict = None
                proxy_error: Optional[str] = None
                if browser_proxy_url:
                    proxy_dict, proxy_error = _parse_proxy_url(browser_proxy_url)
                    if proxy_error:
                        console.print(f"[yellow]{proxy_error}[/yellow]")
                        proxy_dict = None

                browser_hls, browser_files, browser_title, browser_translations, browser_entries = fetch_media_urls_with_browser(
                    page_url,
                    cookies_path=use_cookies,
                    proxy=proxy_dict,
                    translation_hash=selected_translation_hash,
                )
                if browser_title:
                    page_title = browser_title
                if browser_translations:
                    translations = browser_translations
                if browser_entries:
                    hls_entries = browser_entries
                if browser_hls or browser_files:
                    console.print("[green]Браузерный парсер нашёл ссылки.[/green]")
                else:
                    console.print("[yellow]Браузерный парсер не нашёл ссылок.[/yellow]")
                hls_urls = browser_hls
                file_urls = browser_files
                used_browser = True

            if not hls_urls and not file_urls:
                console.print("[yellow]На странице не найдено ни одного видео или HLS-потока.[/yellow]")
                if not use_browser_parser or used_browser:
                    console.print("[dim]Попробуйте указать другой URL или обновить cookies.[/dim]")
                else:
                    console.print("[dim]Можно повторить с опцией --use-browser-parser.[/dim]")
                console.print()
                continue

            def _select_quality(entries: List[Dict[str, str]]) -> tuple[List[Dict[str, str]], Optional[str]]:
                if not entries:
                    return entries, None
                quality_map: "OrderedDict[str, List[Dict[str, str]]]" = OrderedDict()
                for entry in entries:
                    quality_label = entry.get("quality") or ""
                    label = quality_label or "Неизвестно"
                    quality_map.setdefault(label, []).append(entry)
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

            selected_quality_label: Optional[str] = None
            filtered_hls_entries: List[Dict[str, str]] = hls_entries
            if hls_entries:
                filtered_hls_entries, selected_quality_label = _select_quality(hls_entries)
                if not filtered_hls_entries:
                    continue
                hls_urls = [entry.get("url") or "" for entry in filtered_hls_entries]
                hls_urls = [u for u in hls_urls if u]

            if used_browser:
                console.print("[dim]Результаты получены в браузерном режиме.[/dim]")
            if page_title:
                console.print(f"[bold]{page_title}[/bold]")
            if selected_translation_name:
                console.print(f"[bold]Перевод: {selected_translation_name}[/bold]")
            if selected_quality_label:
                console.print(f"[bold]Качество: {selected_quality_label}[/bold]")

            # Формируем единый нумерованный список для выбора
            console.print("[bold]Найденные видео и потоки:[/bold]")
            indexed: list[dict[str, Optional[str]]] = []

            if filtered_hls_entries:
                console.print("HLS (m3u8):")
                for idx, entry in enumerate(filtered_hls_entries, start=1):
                    url_value = entry.get("url") or ""
                    if not url_value:
                        continue
                    label = f"HLS #{idx}"
                    quality_label = entry.get("quality") or "Неизвестно"
                    console.print(f"  {len(indexed)+1}. {label} ({quality_label}): {url_value}")
                    indexed.append(
                        {
                            "label": label,
                            "url": url_value,
                            "title": page_title or label,
                            "translation_name": selected_translation_name,
                            "quality": entry.get("quality"),
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
                import time

                results: list[dict] = []
                batch_started = time.perf_counter()

                for entry in indexed:
                    label = entry.get("label") or ""
                    selected_url = entry.get("url") or ""
                    if not selected_url:
                        console.print(f"[yellow]Пропуск записи {label}: отсутствует URL.[/yellow]")
                        continue
                    translation_name = entry.get("translation_name")
                    quality_name = entry.get("quality")
                    if translation_name:
                        console.print(f"[dim]Перевод: {translation_name}[/dim]")
                    if quality_name:
                        console.print(f"[dim]Качество: {quality_name}[/dim]")
                    desired_name = entry.get("title") or label
                    if translation_name:
                        desired_name = f"{desired_name} - {translation_name}"
                    if quality_name:
                        desired_name = f"{desired_name} - {quality_name}"
                    if desired_name and label and label not in desired_name:
                        desired_name = f"{desired_name} - {label}"
                    success, file_path, elapsed_s, size_bytes, checksum_hex = _run_download(
                        url=selected_url,
                        output_path="Downloads",
                        cookies_path=use_cookies,
                        fmt=None,
                        desired_basename=desired_name,
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
                            "checksum": checksum_hex,
                            "translation": translation_name,
                            "quality": quality_name,
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
                    checksum_text = r.get("checksum") or "n/a"
                    translation_text = f", перевод: {r['translation']}" if r.get("translation") else ""
                    quality_text = f", качество: {r['quality']}" if r.get("quality") else ""
                    console.print(
                        f"- {r['label']}: {status}, файл: {r['filename']}, "
                        f"время: {_fmt_duration(r['elapsed_s'])}, размер: {size_text}, "
                        f"SHA-256: {checksum_text}{translation_text}{quality_text}"
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

            entry = indexed[choice_idx - 1]
            selected_url = entry.get("url") or ""
            if not selected_url:
                console.print("[yellow]URL отсутствует, загрузка пропущена.[/yellow]")
                console.print()
                continue
            label = entry.get("label") or ""
            translation_name = entry.get("translation_name")
            quality_name = entry.get("quality")
            if translation_name:
                console.print(f"[dim]Перевод: {translation_name}[/dim]")
            if quality_name:
                console.print(f"[dim]Качество: {quality_name}[/dim]")
            desired_name = entry.get("title") or label
            if translation_name:
                desired_name = f"{desired_name} - {translation_name}"
            if quality_name:
                desired_name = f"{desired_name} - {quality_name}"
            if desired_name and label and label not in desired_name:
                desired_name = f"{desired_name} - {label}"
            # Для найденных ссылок не навязываем формат — пусть ядро само решит
            _run_download(
                url=selected_url,
                output_path="Downloads",
                cookies_path=use_cookies,
                fmt=None,
                desired_basename=desired_name,
            )
            console.print()
            continue
        elif choice == 5:
            # Поиск видео на специфичных сайтах через site-specific адаптеры
            import os

            page_url = click.prompt("Введите URL страницы", type=str)

            # Определяем cookies так же, как в режиме скачивания видео
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            default_cookies = os.path.join(project_root, "tools", "cookies.txt")
            use_cookies = default_cookies if os.path.isfile(default_cookies) else None

            # Автоопределение адаптера по URL
            adapter = get_adapter_for_url(page_url)
            if not adapter:
                console.print("[yellow]Для данного сайта не найден специфичный адаптер.[/yellow]")
                console.print("[dim]Попробуйте использовать пункт 4 'Найти видео на странице' для универсального парсинга.[/dim]")
                console.print()
                continue

            console.print(f"[cyan]Найден адаптер: {adapter.name}[/cyan]")
            console.print("[dim]Обработка через site-specific парсер...[/dim]")

            page_title: Optional[str] = None
            translations: Optional[List[Dict[str, str]]] = None
            hls_entries: List[Dict[str, str]] = []
            selected_translation_hash: Optional[str] = None
            selected_translation_name: Optional[str] = None

            try:
                hls_urls, file_urls, page_title, translations, hls_entries = adapter.parse(
                    page_url, cookies_path=use_cookies
                )
            except Exception as exc:
                console.print(f"[red]Не удалось найти видео на странице[/red]: {exc}")
                console.print()
                continue

            if translations:
                # Определяем, являются ли это качествами или переводами
                # Если все элементы имеют поле "quality" и их имена похожи на качества (1080p, 720p и т.д.)
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
                        # Для качеств предлагаем выбрать максимальное по умолчанию (первое в отсортированном списке)
                        # или можно выбрать конкретное
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
                        hls_urls, file_urls, page_title, _, hls_entries = adapter.parse(
                            page_url,
                            cookies_path=use_cookies,
                            translation_hash=selected_translation_hash,
                        )
                    except Exception as exc:
                        if is_quality_list:
                            console.print(f"[red]Не удалось получить ссылки для выбранного качества[/red]: {exc}")
                        else:
                            console.print(f"[red]Не удалось получить ссылки для выбранного перевода[/red]: {exc}")
                        console.print()
                        continue
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

            def _select_quality(entries: List[Dict[str, str]]) -> tuple[List[Dict[str, str]], Optional[str]]:
                if not entries:
                    return entries, None
                quality_map: "OrderedDict[str, List[Dict[str, str]]]" = OrderedDict()
                for entry in entries:
                    quality_label = entry.get("quality") or ""
                    label = quality_label or "Неизвестно"
                    quality_map.setdefault(label, []).append(entry)
                
                # Если только одно качество и один поток - возвращаем без запроса
                if len(quality_map) == 1 and len(entries) == 1:
                    label = next(iter(quality_map))
                    return quality_map[label], label if label else None
                
                # Если несколько качеств или несколько потоков - предлагаем выбор
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
                    # Если выбрано качество с несколькими потоками, предлагаем выбрать конкретный поток
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
            filtered_hls_entries: List[Dict[str, str]] = hls_entries
            if hls_entries:
                filtered_hls_entries, selected_quality_label = _select_quality(hls_entries)
                if not filtered_hls_entries:
                    continue
                hls_urls = [entry.get("url") or "" for entry in filtered_hls_entries]
                hls_urls = [u for u in hls_urls if u]

            if page_title:
                console.print(f"[bold]{page_title}[/bold]")
            if selected_translation_name:
                console.print(f"[bold]Перевод: {selected_translation_name}[/bold]")
            if selected_quality_label:
                console.print(f"[bold]Качество: {selected_quality_label}[/bold]")

            # Формируем единый нумерованный список для выбора
            console.print("[bold]Найденные видео и потоки:[/bold]")
            indexed: list[dict[str, Optional[str]]] = []

            if filtered_hls_entries:
                console.print("HLS (m3u8):")
                for idx, entry in enumerate(filtered_hls_entries, start=1):
                    url_value = entry.get("url") or ""
                    if not url_value:
                        continue
                    label = f"HLS #{idx}"
                    quality_label = entry.get("quality") or "Неизвестно"
                    console.print(f"  {len(indexed)+1}. {label} ({quality_label}): {url_value}")
                    indexed.append(
                        {
                            "label": label,
                            "url": url_value,
                            "title": page_title or label,
                            "translation_name": selected_translation_name,
                            "quality": entry.get("quality"),
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
                import time

                results: list[dict] = []
                batch_started = time.perf_counter()

                for entry in indexed:
                    label = entry.get("label") or ""
                    selected_url = entry.get("url") or ""
                    if not selected_url:
                        console.print(f"[yellow]Пропуск записи {label}: отсутствует URL.[/yellow]")
                        continue
                    translation_name = entry.get("translation_name")
                    quality_name = entry.get("quality")
                    if translation_name:
                        console.print(f"[dim]Перевод: {translation_name}[/dim]")
                    if quality_name:
                        console.print(f"[dim]Качество: {quality_name}[/dim]")
                    desired_name = entry.get("title") or label
                    if translation_name:
                        desired_name = f"{desired_name} - {translation_name}"
                    if quality_name:
                        desired_name = f"{desired_name} - {quality_name}"
                    if desired_name and label and label not in desired_name:
                        desired_name = f"{desired_name} - {label}"
                    success, file_path, elapsed_s, size_bytes, checksum_hex = _run_download(
                        url=selected_url,
                        output_path="Downloads",
                        cookies_path=use_cookies,
                        fmt=None,
                        desired_basename=desired_name,
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
                            "checksum": checksum_hex,
                            "translation": translation_name,
                            "quality": quality_name,
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
                    checksum_text = r.get("checksum") or "n/a"
                    translation_text = f", перевод: {r['translation']}" if r.get("translation") else ""
                    quality_text = f", качество: {r['quality']}" if r.get("quality") else ""
                    console.print(
                        f"- {r['label']}: {status}, файл: {r['filename']}, "
                        f"время: {_fmt_duration(r['elapsed_s'])}, размер: {size_text}, "
                        f"SHA-256: {checksum_text}{translation_text}{quality_text}"
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

            entry = indexed[choice_idx - 1]
            selected_url = entry.get("url") or ""
            if not selected_url:
                console.print("[yellow]URL отсутствует, загрузка пропущена.[/yellow]")
                console.print()
                continue
            label = entry.get("label") or ""
            translation_name = entry.get("translation_name")
            quality_name = entry.get("quality")
            if translation_name:
                console.print(f"[dim]Перевод: {translation_name}[/dim]")
            if quality_name:
                console.print(f"[dim]Качество: {quality_name}[/dim]")
            desired_name = entry.get("title") or label
            if translation_name:
                desired_name = f"{desired_name} - {translation_name}"
            if quality_name:
                desired_name = f"{desired_name} - {quality_name}"
            if desired_name and label and label not in desired_name:
                desired_name = f"{desired_name} - {label}"
            # Для найденных ссылок не навязываем формат — пусть ядро само решит
            _run_download(
                url=selected_url,
                output_path="Downloads",
                cookies_path=use_cookies,
                fmt=None,
                desired_basename=desired_name,
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
@click.option(
    "--use-browser-parser/--no-browser-parser",
    "use_browser_parser",
    default=True,
    show_default=True,
    help="Включает автоматический переход в Playwright, если статический парсер ничего не нашёл.",
)
@click.option(
    "--browser-proxy",
    "browser_proxy_url",
    default=None,
    help="Прокси для браузерного парсера (пример: http://user:pass@host:3128).",
)
@click.option(
    "--cookies-from-browser",
    "cookies_from_browser",
    default=None,
    help="Извлечь cookies из браузера (chrome, firefox, edge, brave, opera, vivaldi). Требуется быть залогиненным в YouTube.",
)
def main(
    url: Optional[str],
    output_path: str,
    cookies_path: Optional[str],
    use_browser_parser: bool,
    browser_proxy_url: Optional[str],
    cookies_from_browser: Optional[str],
) -> None:
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
        _run_download(url=url, output_path=output_path, cookies_path=cookies_path, fmt=None,
                      cookies_from_browser=cookies_from_browser)
    else:
        _show_menu_and_handle(use_browser_parser=use_browser_parser, browser_proxy_url=browser_proxy_url)


if __name__ == "__main__":
    main()

