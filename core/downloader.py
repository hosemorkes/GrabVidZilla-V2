"""
Модуль для загрузки видео с использованием yt-dlp.

Этот модуль содержит бизнес-логику загрузки видео.
Не должен импортировать cli или ui пакеты.
"""

from __future__ import annotations

import os
from typing import Callable, Optional, Tuple, List, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from threading import Event

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# Простое условное логирование в файл для отладки (включается через переменную окружения GVZ_DEBUG=1)
def _debug(message: str) -> None:
    try:
        if os.getenv("GVZ_DEBUG", "").strip() not in ("1", "true", "yes"):
            return
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        tools_dir = os.path.join(project_root, "tools")
        os.makedirs(tools_dir, exist_ok=True)
        log_path = os.path.join(tools_dir, "debug.log")
        from datetime import datetime
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n")
    except Exception:
        # Логи не должны ломать логику
        pass


def _normalize_youtube_url(src_url: str) -> str:
    """
    Приводит YouTube URL к более совместимому виду:
    - Shorts → https://www.youtube.com/watch?v=<id>
    - Watch с лишними параметрами → оставляем только v (https://www.youtube.com/watch?v=<id>)
    """
    try:
        from urllib.parse import urlparse, parse_qs
        url = src_url.strip()
        lowered = url.lower()
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path
        if "youtube.com" in host:
            # Shorts → watch
            if "/shorts/" in lowered:
                after = url.split("/shorts/", 1)[1]
                video_id = after.split("?", 1)[0].split("&", 1)[0].strip("/")
                if video_id:
                    return f"https://www.youtube.com/watch?v={video_id}"
            # Watch с множеством параметров → оставим только v
            if path.startswith("/watch"):
                qs = parse_qs(parsed.query or "")
                v = qs.get("v", [None])[0]
                if v:
                    return f"https://www.youtube.com/watch?v={v}"
    except Exception:
        pass
    return src_url


def _filter_valid_formats(formats: list[dict]) -> list[dict]:
    """
    Убирает storyboard (format_id начинается с 'sb') и экзотические контейнеры mhtml.
    Пустое ext допускаем — такие потоки встречаются, но фильтруем только sb*/mhtml.
    """
    valid: list[dict] = []
    for f in formats or []:
        fmt_id = str(f.get("format_id"))
        if not fmt_id or fmt_id.startswith("sb"):
            continue
        ext = (f.get("ext") or "").lower()
        if ext in ("mhtml",):
            continue
        valid.append(f)
    return valid


class DownloadCancelled(RuntimeError):
    """
    Исключение, сигнализирующее о корректной отмене загрузки пользователем.
    Используется для прерывания процесса в хукe прогресса.
    """


def extract_info_multi(
    url: str,
    cookies_path: str | None = None,
    po_token: str | None = None,
) -> tuple[dict, str]:
    """
    Извлекает info (без скачивания) поочерёдно разными YouTube клиентами.
    Возвращает (info, used_client). Использует process=False и фильтрует форматы.
    """
    url = _normalize_youtube_url(url)
    # Порядок клиентов подобран эмпирически для Shorts и случаев без JS/PO-token:
    # web → android_sdkless → ios → tv → web_safari
    clients: list[str] = ["web", "android_sdkless", "ios", "tv", "web_safari"]
    if po_token:
        clients.append("android")
    last_err: Exception | None = None

    for client in clients:
        ydl_opts: dict = {
            "quiet": True,
            "noprogress": True,
            "no_warnings": True,
            "skip_download": True,
            "logger": None,
            "geo_bypass": True,
            "retries": 2,
            "extractor_retries": 1,
            "socket_timeout": 15,
            "extractor_args": {
                "youtube": {
                    "player_client": [client],
                    # Дадим yt-dlp время между extract и download на подготовку форматов
                    "playback_wait": ["6"],
                },
            },
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
            },
        }
        if cookies_path:
            ydl_opts["cookiefile"] = cookies_path
        if client == "android" and po_token:
            ydl_opts.setdefault("extractor_args", {}).setdefault("youtube", {})["po_token"] = [po_token]

        try:
            with YoutubeDL(ydl_opts) as ydl:
                # 1) сначала «сырой» список форматов
                info = ydl.extract_info(url, download=False, process=False)  # type: ignore[arg-type]
            fmts_raw = _filter_valid_formats(info.get("formats") or [])
            _debug(f"extract_info_multi: client={client} raw_formats={len(fmts_raw)}")
            if not fmts_raw:
                # 2) попробуем с обработкой (process=True) — частый случай для Shorts
                with YoutubeDL(ydl_opts) as ydl2:
                    info = ydl2.extract_info(url, download=False, process=True)  # type: ignore[arg-type]
                fmts_proc = _filter_valid_formats(info.get("formats") or [])
                info["formats"] = fmts_proc
                _debug(f"extract_info_multi: client={client} proc_formats={len(fmts_proc)}")
                if fmts_proc:
                    info["gvz_used_client"] = client
                    return info, client
                last_err = RuntimeError("no valid formats after filtering (process/raw)")
            else:
                info["formats"] = fmts_raw
                info["gvz_used_client"] = client
                return info, client
        except Exception as e:
            _debug(f"extract_info_multi: client={client} error={e}")
            last_err = e
            continue

    if last_err:
        raise RuntimeError(f"Не удалось извлечь форматы (пробовали: {clients}): {last_err}") from last_err
    raise RuntimeError("Не удалось извлечь форматы: неизвестная ошибка")


def download_video(
    url: str,
    output_path: str = ".",
    progress_callback: Callable[[float], None] | None = None,
    progress_info_callback: Callable[[dict], None] | None = None,
    cancel_event: "Optional[Event]" = None,
    cookies_path: str | None = None,
    format: str | None = None,
    audio_only: bool = False,
    subtitle_lang: str | None = None,
) -> str:
    """
    Загружает видео по URL.
    
    Args:
        url: URL видео для загрузки
        output_path: Путь для сохранения (по умолчанию текущая директория)
        progress_callback: Функция обратного вызова для прогресса (принимает float от 0 до 100)
        progress_info_callback: Доп. колбек с подробной информацией (speed, bytes, total)
        cancel_event: Опциональный Event для отмены загрузки (устанавливается извне)
        cookies_path: Путь к cookies.txt (формат Netscape). Если указан — передаётся yt-dlp
        format: Желаемый формат (опционально)
        audio_only: Загрузить только аудио (True) или видео+аудио (False)
    
    Returns:
        Путь к загруженному файлу
    
    Raises:
        ValueError: Если URL некорректен
        RuntimeError: Если загрузка не удалась
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL должен быть непустой строкой")

    # Простая валидация схемы URL
    lowered = url.strip().lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        raise ValueError("URL должен начинаться с http:// или https://")

    # Нормализуем Shorts-ссылки
    url = _normalize_youtube_url(url)
    _debug(f"download_video: normalized_url={url}")

    # Приведение пути и подготовка каталога
    target_dir = os.path.abspath(output_path or ".")
    os.makedirs(target_dir, exist_ok=True)

    # Шаблон имени файла с ID (стабильно для yt-dlp); позже переименуем в «чистое» имя
    outtmpl = os.path.join(target_dir, "%(title)s [%(id)s].%(ext)s")

    # Контейнер для имени файла, который сообщает yt-dlp по завершении скачивания
    # Используем список как изменяемую обёртку, чтобы замыкание могло присвоить значение
    last_filename: list[str | None] = [None]

    def _progress_hook(d: dict) -> None:
        """
        Хук прогресса от yt-dlp вызывается при изменении статуса скачивания.
        Здесь мы:
        - когда status == "downloading": считаем процент как downloaded_bytes / total_bytes
          (если total неизвестен, берём total_bytes_estimate). Если есть колбек, передаём ему
          значение процента от 0 до 100.
        - когда status == "finished": запоминаем путь к файлу, который сформировал yt-dlp.
        """
        status = d.get("status")
        # Проверка на отмену — максимально рано, чтобы прерывать быстро
        try:
            if cancel_event is not None and cancel_event.is_set():
                # Почистим временные файлы, если доступны пути
                tmp = d.get("tmpfilename") or d.get("filename")
                if isinstance(tmp, str):
                    try:
                        if os.path.exists(tmp):
                            os.remove(tmp)
                    except Exception:
                        pass
                raise DownloadCancelled("Загрузка отменена пользователем")
        except DownloadCancelled:
            # Пробрасываем дальше, чтобы yt-dlp прекратил работу
            raise
        except Exception:
            # Любые ошибки в блоке отмены не должны ломать основной процесс
            pass
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            # Передаём скорость и байты, если колбек указан
            if progress_info_callback is not None:
                try:
                    progress_info_callback(
                        {
                            "speed": d.get("speed"),
                            "downloaded_bytes": downloaded,
                            "total_bytes": total,
                        }
                    )
                except Exception:
                    pass
            if total and total > 0:
                percent = float(downloaded) / float(total) * 100.0
                # Ограничим диапазон на всякий случай
                percent = max(0.0, min(100.0, percent))
                if progress_callback is not None:
                    try:
                        progress_callback(percent)
                    except Exception:
                        # Ошибки в пользовательском колбэке не должны срывать загрузку
                        pass
        elif status == "finished":
            # yt-dlp сообщает путь к итоговому файлу после скачивания
            last_filename[0] = d.get("filename")

    # Тихий логгер, чтобы yt-dlp не печатал ошибки напрямую (избегаем дублей в CLI)
    class _SilentLogger:
        def debug(self, msg: str) -> None:
            pass
        def warning(self, msg: str) -> None:
            pass
        def error(self, msg: str) -> None:
            pass

    # Базовые опции yt-dlp: тихий режим, свой шаблон имени и наш хук прогресса
    ydl_opts: dict = {
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "outtmpl": outtmpl,
        "progress_hooks": [_progress_hook],
        "logger": _SilentLogger(),
        # Базовые заголовки — притворяемся обычным браузером
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        },
        # Мягкие улучшения совместимости для YouTube без авторизации
        "extractor_args": {
            "youtube": {
                # Используем web-клиент для большей стабильности форматов
                "player_client": ["web"]
            }
        },
        "geo_bypass": True,
        "retries": 3,
        "fragment_retries": 3,
    }
    if cookies_path:
        ydl_opts["cookiefile"] = cookies_path
    # Субтитры: скачиваем и встраиваем, если выбран язык
    if subtitle_lang:
        ydl_opts.update({
            "writesubtitles": True,
            "subtitleslangs": [subtitle_lang],
            "subtitlesformat": "srt",
            "embedsubtitles": True,  # встраиваем в итоговый контейнер (mp4/webm/mkv)
        })

    # Базовый выбор формата (будет уточнён после анализа info ниже)
    ydl_opts["format"] = format or "bv*+ba/best"

    # Шаг 1: извлечём info без скачивания (с фолбэком клиентов), чтобы подобрать реально доступный формат
    try:
        po_token = os.getenv("YT_PO_TOKEN")
        info, used_client = extract_info_multi(url, cookies_path=cookies_path, po_token=po_token)
        sample_ids = [str(f.get("format_id")) for f in (info.get("formats") or [])[:8]]
        _debug(f"download_video: probe_ok clients_used={used_client} formats={len(info.get('formats', []))} ids_sample={sample_ids}")
    except Exception as e:
        raise RuntimeError(f"Ошибка анализа перед загрузкой: {e}") from e

    # Подберём безопасный формат на основе info
    desired_height: int | None = None
    if audio_only:
        desired_height = None
    else:
        # Попробуем извлечь высоту из переданного format (например, 'height<=1080')
        if isinstance(format, str) and "height<=" in format:
            import re
            m = re.search(r"height<=\\s*(\\d+)", format)
            if m:
                try:
                    desired_height = int(m.group(1))
                except Exception:
                    desired_height = None

    # Сформируем выражение выбора формата в терминах yt-dlp (см. README: FORMAT SELECTION)
    if audio_only:
        fmt_selector = "bestaudio/best"
    elif desired_height:
        fmt_selector = f"bv*[height<={desired_height}]+ba/b[height<={desired_height}]"
    else:
        fmt_selector = "bv*+ba/b"
    _debug(f"download_video: format_selector={fmt_selector} desired_height={desired_height} audio_only={audio_only}")

    # Шаг 2: непосредственно загрузка с подобранным форматом
    try:
        dl_opts = dict(ydl_opts)
        dl_opts["format"] = fmt_selector
        if "skip_download" in dl_opts:
            dl_opts.pop("skip_download", None)
        # Используем тот же клиент, что и при анализе
        dl_opts.setdefault("extractor_args", {}).setdefault("youtube", {})["player_client"] = [used_client]
        if used_client == "android" and os.getenv("YT_PO_TOKEN"):
            dl_opts["extractor_args"]["youtube"]["po_token"] = [os.getenv("YT_PO_TOKEN")]  # type: ignore[index]
        with YoutubeDL(dl_opts) as ydl:
            info2 = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info2)
        _debug(f"download_video: downloaded filepath={filepath}")

        if not filepath:
            filepath = last_filename[0]
        if not filepath:
            raise RuntimeError("Не удалось определить путь загруженного файла")
        return str(filepath)

    except DownloadCancelled as e:
        _debug("download_video: cancelled by user")
        raise
    except DownloadError as e:
        _debug(f"download_video: error {e}")
        raise RuntimeError(f"Ошибка загрузки: {e}") from e


def probe_video(
    url: str,
    cookies_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Возвращает метаданные и доступные форматы видео без скачивания.

    Args:
        url: URL контента
        cookies_path: путь к cookies.txt при необходимости

    Returns:
        Словарь info как возвращает yt-dlp (отфильтрованные форматы в поле 'formats').

    Raises:
        ValueError | RuntimeError при ошибках валидации/извлечения.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL должен быть непустой строкой")
    lowered = url.strip().lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        raise ValueError("URL должен начинаться с http:// или https://")
    url = _normalize_youtube_url(url)
    try:
        po_token = os.getenv("YT_PO_TOKEN")
        info, _ = extract_info_multi(url, cookies_path=cookies_path, po_token=po_token)
        return info
    except Exception as e:
        raise RuntimeError(f"Не удалось извлечь информацию о видео: {e}") from e

def analyze_video(
    url: str,
    cookies_path: str | None = None,
) -> tuple[dict, list[str], list[str]]:
    """
    Анализирует видео по URL без скачивания.

    Возвращает кортеж:
      - info: словарь с метаданными ролика от yt-dlp
      - qualities: список строк качеств (например, '2160p', '1080p', '720p', 'audio only')
      - subtitle_langs: список языков субтитров (['en', 'ru', ...])

    Args:
        url: URL видео для анализа
        cookies_path: Путь к cookies.txt (формат Netscape) при необходимости

    Raises:
        ValueError: Если URL некорректен
        RuntimeError: Если анализ не удался
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL должен быть непустой строкой")

    lowered = url.strip().lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        raise ValueError("URL должен начинаться с http:// или https://")

    # Нормализуем Shorts-ссылки
    url = _normalize_youtube_url(url)
    _debug(f"analyze_video: normalized_url={url}")

    try:
        po_token = os.getenv("YT_PO_TOKEN")
        info, used_client = extract_info_multi(url, cookies_path=cookies_path, po_token=po_token)
        _debug(f"analyze_video: extracted formats={len(info.get('formats', []))} client={used_client}")
        # Если в форматах отсутствуют высоты (или сильно урезаны), попробуем обогащённую обработку (process=True)
        fmts = info.get("formats") or []
        missing_heights = any((f.get("vcodec") and f.get("vcodec") != "none") and not f.get("height") for f in fmts)
        max_h_seen = max([int(f.get("height") or 0) for f in fmts] + [0])
        if missing_heights or max_h_seen < 2000:
            try:
                ydl_opts = {
                    "quiet": True,
                    "noprogress": True,
                    "no_warnings": True,
                    "skip_download": True,
                    "logger": None,
                    "geo_bypass": True,
                    "retries": 2,
                    "extractor_retries": 1,
                    "socket_timeout": 15,
                    "extractor_args": {
                        "youtube": {
                            "player_client": [used_client],
                            "playback_wait": ["6"],
                        },
                    },
                }
                if cookies_path:
                    ydl_opts["cookiefile"] = cookies_path
                if used_client == "android" and po_token:
                    ydl_opts.setdefault("extractor_args", {}).setdefault("youtube", {})["po_token"] = [po_token]
                with YoutubeDL(ydl_opts) as ydl:
                    info2 = ydl.extract_info(url, download=False, process=True)  # type: ignore[arg-type]
                info2["formats"] = _filter_valid_formats(info2.get("formats") or [])
                fmts2 = info2.get("formats") or []
                max_h2 = max([int(f.get("height") or 0) for f in fmts2] + [0])
                if fmts2 and max_h2 >= max_h_seen:
                    info = info2
                    _debug(f"analyze_video: enriched formats via process=True, formats={len(fmts2)} max_h={max_h2}")
            except Exception as e2:
                _debug(f"analyze_video: enrich process=True failed: {e2}")
                # Без паники: оставляем первоначальные данные
                pass
    except Exception as e:
        _debug(f"analyze_video: error {e}")
        raise RuntimeError(f"Ошибка анализа: {e}") from e

    # Собираем качества, предпочитая метку YouTube (quality_label), чтобы 3840x1632 отображалось как "2160p"
    labels: dict[int, str] = {}
    import re as _re
    for f in info.get("formats", []):
        vcodec = f.get("vcodec")
        if not vcodec or vcodec == "none":
            continue
        label = f.get("quality_label") or f.get("format_note")
        height = f.get("height")
        # Извлечём числовое качество из label, иначе используем height
        val: int | None = None
        if isinstance(label, str):
            m = _re.search(r"(\d{3,4})p", label)
            if m:
                try:
                    val = int(m.group(1))
                except Exception:
                    val = None
        if val is None and height:
            try:
                val = int(height)
            except Exception:
                val = None
        if val is None:
            continue
        # Сохраняем наиболее информативную метку
        shown = label if isinstance(label, str) and "p" in label else f"{val}p"
        # Если уже есть, оставим более длинную (обычно содержит fps/HDR)
        if val not in labels or (len(shown) > len(labels[val])):
            labels[val] = shown
    qualities: list[str] = [labels[k] for k in sorted(labels.keys(), reverse=True)]
    if "audio only" not in qualities:
        qualities.append("audio only")

    # Языки субтитров
    subtitle_langs: list[str] = []
    subs = info.get("subtitles") or {}
    if isinstance(subs, dict):
        subtitle_langs = sorted(list(subs.keys()))

    return info, qualities, subtitle_langs


def select_format_for_height(
    info: dict,
    max_height: int | None,
    audio_only: bool = False
) -> str:
    """
    Подбирает надёжную строку формата на основе уже извлечённого info.

    - Если audio_only: возвращает 'bestaudio/best'.
    - Иначе подбирает лучшую видеодорожку (vcodec != 'none') с высотой <= max_height,
      и лучшую аудиодорожку (acodec != 'none'); если аудио отдельно не найдено,
      вернёт одиночный прогрессивный формат (video+audio в одном).

    Всегда возвращает корректный формат-id или 'best' как последний фолбэк.
    """
    if audio_only:
        # Попробуем вернуть конкретный аудио format_id, если он есть (исключая storyboard и нетипичные контейнеры)
        fmts = info.get("formats") or []
        audio_streams_only = []
        for f in fmts:
            fmt_id = str(f.get("format_id"))
            if not fmt_id or fmt_id.startswith("sb"):  # storyboard форматы
                continue
            ext = (f.get("ext") or "").lower()
            if ext in ("mhtml", ""):
                continue
            vcodec = f.get("vcodec")
            acodec = f.get("acodec")
            if (not vcodec or vcodec == "none") and (acodec and acodec != "none"):
                # Приоритет понятных контейнеров
                preference = 2 if ext in ("m4a", "webm", "mp4") else 1
                abr = int(f.get("abr") or 0)
                audio_streams_only.append((preference, abr, f))
        if audio_streams_only:
            audio_streams_only.sort(key=lambda x: (x[0], x[1]), reverse=True)
            return str(audio_streams_only[0][2].get("format_id"))
        return "bestaudio/best"

    formats = list(info.get("formats") or [])
    if not formats:
        return "best"

    # Отфильтруем «служебные» форматы (storyboard sb*, mhtml и пр.)
    filtered_formats = []
    for f in formats:
        fmt_id = str(f.get("format_id"))
        if not fmt_id or fmt_id.startswith("sb"):
            continue
        ext = (f.get("ext") or "").lower()
        if ext in ("mhtml", ""):
            continue
        filtered_formats.append(f)
    formats = filtered_formats
    if not formats:
        return "best"

    # Отдельные списки
    video_streams = []
    audio_streams = []
    progressive = []  # контейнеры, где есть и видео, и аудио

    for f in formats:
        vcodec = f.get("vcodec")
        acodec = f.get("acodec")
        height = f.get("height")
        fmt_id = f.get("format_id")
        if not fmt_id:
            continue

        if vcodec and vcodec != "none":
            # Видеодорожка
            if height is None:
                # неизвестная высота — ставим 0 для сортировки
                h = 0
            else:
                h = int(height)
            if acodec and acodec != "none":
                # Прогрессивное видео (video+audio)
                progressive.append((h, f))
            else:
                video_streams.append((h, f))
        else:
            # Нет видеокодека — возможно аудиопоток
            if acodec and acodec != "none":
                # Попробуем использовать abr как сортировку
                abr = f.get("abr") or 0
                audio_streams.append((int(abr), f))

    # Сортировки: видеопотоки по высоте, аудио по битрейту
    video_streams.sort(key=lambda x: (x[0], x[1].get('tbr') or 0), reverse=True)
    progressive.sort(key=lambda x: (x[0], x[1].get('tbr') or 0), reverse=True)
    audio_streams.sort(key=lambda x: x[0], reverse=True)

    # Если задана max_height — сначала пробуем подходящие по высоте
    def pick_video(max_h: int | None):
        if max_h is None:
            return video_streams[0][1] if video_streams else None
        for h, f in video_streams:
            if h and h <= max_h:
                return f
        # если ни один не подошёл — возьмём самый маленький доступный
        return video_streams[-1][1] if video_streams else None

    def pick_progressive(max_h: int | None):
        if max_h is None:
            return progressive[0][1] if progressive else None
        for h, f in progressive:
            if h and h <= max_h:
                return f
        return progressive[-1][1] if progressive else None

    if progressive:
        pass  # Больше не отдаём приоритет прогрессивным форматам

    # Отдельные дорожки video + audio
    v = pick_video(max_height)
    a = audio_streams[0][1] if audio_streams else None

    if v and a:
        return f"{v.get('format_id')}+{a.get('format_id')}"
    if v:
        return str(v.get("format_id"))

    # Если не нашлось отдельных дорожек — используем прогрессивные
    if progressive:
        prog = pick_progressive(max_height)
        if prog is not None:
            return str(prog.get("format_id"))

    # Если ничего не подошло — вернём первый доступный format_id из списка
    if formats and formats[0].get("format_id"):
        return str(formats[0].get("format_id"))

    # Фолбэк
    return "best"