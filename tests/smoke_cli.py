"""Smoke-тест CLI GrabVidZilla.

Этот скрипт запускает существующий CLI (`cli/cli.py`) как «чёрный ящик»
через `python -m cli.cli` или через установленный бинарь `grabvidzilla`
(по флагу `--use-binary`) и прогоняет список тестовых ссылок.

Список ссылок редактируется в словаре `TEST_URLS` ниже: ключ — это короткий label для отчёта,
значение — URL. Результаты выводятся в понятном табличном виде и скрипт завершает работу
кодом возврата 0 при успехе и 1 — при наличии ошибок.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
from typing import Dict, List, Optional, Sequence, Set, Tuple


# Определение корня проекта (работаем всегда из него)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# Список тестовых ссылок (редактируйте под себя)
TEST_URLS: Dict[str, str] = {
    "youtube_ok": "https://www.youtube.com/watch?v=zFFsQ-hwTdM",  # пример: https://www.youtube.com/watch?v=dQw4w9WgXcQ
    "youtube_ok_Shorts_ok": "https://www.youtube.com/shorts/7osifm4NFaY",
    "vk_ok": "https://vkvideo.ru/video-2000216646_144216646",
    "vk_клипы_ok": "https://vkvideo.ru/clip771256520_456239596",
    "rutube_ok": "https://rutube.ru/video/59e05f4a6453c7c6401e8c78d4ae31f0/",
    # сюда вы будете добавлять/менять ссылки
}


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Парсинг аргументов командной строки."""
    parser = argparse.ArgumentParser(
        description=(
            "Smoke-тест для GrabVidZilla CLI. "
            "Запускает `python -m cli.cli` или бинарь `grabvidzilla` на наборе URL."
        )
    )
    parser.add_argument(
        "--use-binary",
        action="store_true",
        help="Использовать установленный бинарь `grabvidzilla` вместо `python -m cli.cli`.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help=(
            "Папка для загрузок. Если не указана — будет создана временная директория, "
            "которая удалится при успешном прохождении всех тестов."
        ),
    )
    return parser.parse_args(argv)


def run_single_case(
    label: str,
    url: str,
    output_dir: str,
    use_binary: bool,
) -> Tuple[str, str, str, float, Optional[int], int]:
    """Запускает один кейс CLI и возвращает (label, url, status, duration, returncode)."""
    print(f"== [{label}] {url}")

    if use_binary:
        cmd = ["grabvidzilla", url, "--output", output_dir]
    else:
        cmd = [sys.executable, "-m", "cli.cli", url, "--output", output_dir]

    before_paths = _list_all_files(output_dir)

    start = time.perf_counter()
    status = "FAIL"
    returncode: Optional[int] = None
    new_bytes: int = 0
    parsed_download_seconds: Optional[float] = None

    try:
        rc, captured = _run_command(cmd)
        returncode = rc
        status = "OK" if returncode == 0 else "FAIL"
        parsed_download_seconds = _parse_cli_elapsed_seconds(captured)
    except Exception:
        # Команда не стартанула — считаем FAIL, returncode оставляем None
        status = "FAIL"
    finally:
        wall_seconds = time.perf_counter() - start
        after_paths = _list_all_files(output_dir)
        new_paths = after_paths - before_paths
        new_bytes = _sum_file_sizes(new_paths)

    # В отчёт берём «чистое» время из CLI, если оно распарсилось; иначе — общее время процесса
    duration_seconds = parsed_download_seconds if parsed_download_seconds is not None else wall_seconds
    return (label, url, status, duration_seconds, returncode, new_bytes)


def _run_command(cmd: List[str]) -> Tuple[int, str]:
    """Запускает команду, построчно транслируя вывод в текущий stdout и накапливая его для парсинга.

    Возвращает (returncode, captured_stdout_stderr).
    """
    import subprocess
    env = os.environ.copy()
    # Форсируем UTF-8 в дочернем процессе для корректного вывода эмодзи и кириллицы
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("RICH_ENCODING", "utf-8")

    # Объединяем stderr в stdout, чтобы не потерять сообщения и упростить парсинг
    proc = subprocess.Popen(
        cmd,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        bufsize=1,
    )

    captured_lines: List[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        # Мгновенно транслируем в текущий stdout для видимости прогресса
        sys.stdout.write(line)
        sys.stdout.flush()
        captured_lines.append(line)

    proc.wait()
    captured_text = "".join(captured_lines)
    return proc.returncode, captured_text


def _parse_cli_elapsed_seconds(output_text: str) -> Optional[float]:
    """Парсит из вывода CLI строку 'Время скачивания: ...' и возвращает секунды.

    Поддерживаются форматы:
      - 'Время скачивания: 13.7 сек'
      - 'Время скачивания: 6 мин 0.2 сек'
    """
    # Порядок: сначала сложный формат с минутами, затем простой в секундах
    minutes_pattern = re.compile(r"Время\s+скачивания:\s*(\d+)\s*мин\s*([\d\.]+)\s*сек", re.IGNORECASE)
    seconds_pattern = re.compile(r"Время\s+скачивания:\s*([\d\.]+)\s*сек", re.IGNORECASE)

    m = minutes_pattern.search(output_text)
    if m:
        try:
            mins = int(m.group(1))
            secs = float(m.group(2))
            return float(mins * 60) + secs
        except ValueError:
            return None

    s = seconds_pattern.search(output_text)
    if s:
        try:
            return float(s.group(1))
        except ValueError:
            return None

    return None


def _list_all_files(root_dir: str) -> Set[str]:
    """Возвращает множество путей всех файлов внутри root_dir (рекурсивно)."""
    paths: Set[str] = set()
    if not os.path.isdir(root_dir):
        return paths
    for dirpath, _, filenames in os.walk(root_dir):
        for name in filenames:
            full_path = os.path.join(dirpath, name)
            paths.add(os.path.abspath(full_path))
    return paths


def _sum_file_sizes(paths: Sequence[str]) -> int:
    """Суммирует размеры файлов, пропуская недоступные/удалённые."""
    total = 0
    for p in paths:
        try:
            total += os.path.getsize(p)
        except OSError:
            # Файл могли удалить/переместить, пропускаем
            continue
    return total


def _format_size_mb_gb(num_bytes: int) -> str:
    """Форматирует размер в MB или GB с точностью 0.1. Минимум — 0.1 MB."""
    mb = num_bytes / (1024 * 1024)
    if mb >= 1024:
        gb = mb / 1024
        return f"{gb:5.1f} GB"
    # Гарантируем минимальное отображение 0.1 MB, чтобы не было '0.0 MB'
    mb = max(mb, 0.1 if num_bytes > 0 else 0.0)
    return f"{mb:5.1f} MB"


def format_report_line(
    status: str,
    label: str,
    duration_seconds: float,
    size_bytes: int,
    url: str,
    label_width: int,
) -> str:
    """Форматирует одну строку отчёта."""
    # Статус слева в ширине 4, label — по вычисленной ширине, время с точностью до 0.1
    # В примере используется 'c' (секунды)
    size_h = _format_size_mb_gb(size_bytes)
    return (
        f"{status:<4} | "
        f"{label:<{label_width}} | "
        f"{duration_seconds:5.1f} c | "
        f"{size_h:>9} | "
        f"{url}"
    )


def main(argv: Optional[List[str]] = None) -> int:
    """Точка входа smoke-теста."""
    args = parse_args(argv)

    # Определение выходной директории
    used_temp_dir = False
    if args.output:
        output_dir = os.path.abspath(args.output)
        os.makedirs(output_dir, exist_ok=True)
    else:
        # Используем Downloads/temp в корне проекта по умолчанию
        output_dir = os.path.join(PROJECT_ROOT, "Downloads", "temp")
        os.makedirs(output_dir, exist_ok=True)
        used_temp_dir = True

    # Прогон ссылок
    results: List[Tuple[str, str, str, float, Optional[int], int]] = []
    for label, url in TEST_URLS.items():
        case_result = run_single_case(
            label=label,
            url=url,
            output_dir=output_dir,
            use_binary=args.use_binary,
        )
        results.append(case_result)

    # Итоговый отчёт
    print("\n=== ИТОГОВЫЙ ОТЧЁТ ===")
    any_fail = any(status != "OK" for _, _, status, _, _, _ in results)
    label_width = max((len(label) for label, *_ in results), default=10)
    total_duration = sum(duration for _, _, _, duration, _, _ in results)
    total_bytes = sum(size_b for _, _, _, _, _, size_b in results)

    for label, url, status, duration, _, size_b in results:
        line = format_report_line(
            status=status,
            label=label,
            duration_seconds=duration,
            size_bytes=size_b,
            url=url,
            label_width=label_width,
        )
        print(line)

    # Строка с итогами по времени и размеру
    total_line = (
        f"{'':<4} | "
        f"{'ИТОГО':<{label_width}} | "
        f"{total_duration:5.1f} c | "
        f"{_format_size_mb_gb(total_bytes):>9} | "
        f"{''}"
    )
    print(total_line)

    # Удаление временной директории при полном успехе
    if not any_fail:
        print("✅ Все тесты успешно пройдены")
        print()
        print()
        if used_temp_dir:
            shutil.rmtree(output_dir, ignore_errors=True)
        return 0

    # Есть ошибки — директорию не трогаем
    print("❌ Есть ошибки при проверке CLI GrabVidZilla")
    if used_temp_dir:
        print(f"Загруженные файлы и лог CLI сохранены в: {output_dir}")
    return 1


if __name__ == "__main__":
    sys.exit(main())


