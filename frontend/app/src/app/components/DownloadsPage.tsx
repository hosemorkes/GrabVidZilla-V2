import { useState, useRef, useEffect, useCallback } from "react";
import { Search, Download, Music, Film } from "lucide-react";
import { DownloadCard, DownloadItem, DownloadStatus } from "./DownloadCard";
import { LogConsole, LogEntry } from "./LogConsole";
import api from "@/api/client";

// ─── Вспомогательные функции ─────────────────────────────────────────────────

function getTimestamp() {
  const now = new Date();
  return `${now.getHours().toString().padStart(2, "0")}:${now.getMinutes().toString().padStart(2, "0")}:${now.getSeconds().toString().padStart(2, "0")}`;
}

function extractDomain(url: string) {
  try {
    const u = new URL(url.startsWith("http") ? url : "https://" + url);
    return u.hostname.replace("www.", "");
  } catch {
    return url.length > 20 ? url.slice(0, 20) + "..." : url;
  }
}

/**
 * Строит format selector для yt-dlp по выбранному качеству.
 * Например: "1080p" → "bestvideo[height<=1080]+bestaudio/best[height<=1080]"
 */
function buildFormatSelector(quality: string, audioOnly: boolean): string {
  if (audioOnly) return "bestaudio/best";
  if (!quality || quality === "best") return "bestvideo+bestaudio/best";
  const match = quality.match(/(\d+)p/);
  if (match) {
    const h = match[1];
    return `bestvideo[height<=${h}]+bestaudio/best[height<=${h}]`;
  }
  if (quality.toLowerCase().includes("audio")) return "bestaudio/best";
  return quality; // format_id напрямую
}

// Маппинг статусов API → локальных статусов DownloadCard
const API_STATUS_MAP: Record<string, DownloadStatus> = {
  queued: "pending",
  downloading: "downloading",
  completed: "completed",
  error: "error",
  cancelled: "error",
};

// ─── Типы ────────────────────────────────────────────────────────────────────

interface VideoInfo {
  title: string;
  thumbnail?: string;
  qualities: string[];
  subtitle_langs: string[];
}

// ─── Компонент ───────────────────────────────────────────────────────────────

export function DownloadsPage() {
  const stored = JSON.parse(localStorage.getItem("user") || "{}");
  const isPrivileged = stored.role === "root" || stored.role === "admin";

  const [url, setUrl] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [videoInfo, setVideoInfo] = useState<VideoInfo | null>(null);
  const [selectedQuality, setSelectedQuality] = useState("best");
  const [audioOnly, setAudioOnly] = useState(false);

  const [downloads, setDownloads] = useState<DownloadItem[]>([]);
  const [logs, setLogs] = useState<LogEntry[]>(() => [
    { timestamp: getTimestamp(), message: "Application started successfully.", type: "success" },
    { timestamp: getTimestamp(), message: "Ready to download videos.", type: "info" },
  ]);
  const [downloading, setDownloading] = useState(false);

  // Ref для стабильного доступа к downloads внутри polling-интервала
  const downloadsRef = useRef<DownloadItem[]>([]);
  useEffect(() => {
    downloadsRef.current = downloads;
  }, [downloads]);

  const addLog = useCallback((message: string, type: LogEntry["type"] = "info") => {
    setLogs((prev) => [...prev, { timestamp: getTimestamp(), message, type }]);
  }, []);

  // Polling активных задач из API каждые 2 секунды
  useEffect(() => {
    const poll = async () => {
      const active = downloadsRef.current.filter(
        (d) => (d.status === "downloading" || d.status === "pending") && d.task_id
      );
      for (const item of active) {
        try {
          const { data } = await api.get(`/downloads/${item.task_id}`);
          const newStatus = API_STATUS_MAP[data.status] ?? item.status;
          setDownloads((prev) =>
            prev.map((d) =>
              d.task_id === item.task_id
                ? {
                    ...d,
                    status: newStatus,
                    progress: data.progress ?? d.progress,
                    speed: data.speed ?? d.speed,
                    timeLeft: data.eta ?? d.timeLeft,
                    filename: data.filename ?? d.filename,
                    user_id: data.user_id ?? d.user_id,
                    user_name: data.user_name ?? d.user_name,
                  }
                : d
            )
          );
          if (data.status === "completed") {
            addLog(`✅ Готово: ${data.filename || item.filename}`, "success");
          } else if (data.status === "error") {
            addLog(`❌ ${item.filename}: ${data.error_message || "Неизвестная ошибка"}`, "error");
          }
        } catch {
          // Игнорируем ошибки polling
        }
      }
    };
    const interval = setInterval(poll, 2000);
    return () => clearInterval(interval);
  }, [addLog]);

  // ── Анализ URL ───────────────────────────────────────────────────────────
  const handleAnalysis = async () => {
    if (!url.trim()) return;
    setAnalyzing(true);
    setVideoInfo(null);
    addLog(`Анализ URL: ${url}`, "info");

    try {
      const res = await api.get("/analyze", { params: { url } });
      const info = res.data;
      const qualities: string[] = info.qualities?.length ? info.qualities : ["best"];

      setVideoInfo({
        title: info.info?.title || extractDomain(url),
        thumbnail: info.info?.thumbnail,
        qualities,
        subtitle_langs: info.subtitle_langs || [],
      });
      setSelectedQuality(qualities[0]);
      addLog(`Найдено: "${info.info?.title || extractDomain(url)}"`, "success");
    } catch (err: any) {
      const msg = err.response?.data?.detail || "Не удалось получить информацию о видео";
      addLog(`Ошибка анализа: ${msg}`, "error");
    } finally {
      setAnalyzing(false);
    }
  };

  // ── Запуск скачивания ────────────────────────────────────────────────────
  const handleDownload = async () => {
    if (!videoInfo) return;

    setDownloading(true);
    const domain = extractDomain(url);
    const filename = videoInfo.title
      ? `${videoInfo.title.replace(/[<>:"/\\|?*]/g, "_").substring(0, 80)}`
      : domain;

    // Получаем user_id из сессии для записи источника
    const userRaw = localStorage.getItem("user");
    const userId = userRaw ? JSON.parse(userRaw).user_id : undefined;

    try {
      const { data } = await api.post("/downloads", {
        url,
        format: buildFormatSelector(selectedQuality, audioOnly),
        audio_only: audioOnly,
        source: "ui",
        ...(userId && { user_id: userId }),
      });

      const newItem: DownloadItem = {
        id: Date.now().toString(),
        filename: audioOnly ? `${filename}.mp3` : `${filename}.mp4`,
        status: "downloading",
        task_id: data.id,
        originalUrl: url,
        timeLeft: "—",
        speed: "—",
        source: domain,
        progress: 0,
        user_id: userId ?? null,
      };

      setDownloads((prev) => [...prev, newItem]);
      addLog(`Скачивание запущено: ${newItem.filename}`, "info");

      // Сбрасываем форму для следующего URL
      setVideoInfo(null);
      setUrl("");
      setSelectedQuality("best");
      setAudioOnly(false);
    } catch (err: any) {
      const msg = err.response?.data?.detail || err.message || "Неизвестная ошибка";
      addLog(`Не удалось начать скачивание: ${msg}`, "error");
    } finally {
      setDownloading(false);
    }
  };

  // ── Отмена задачи ────────────────────────────────────────────────────────
  const handleCancel = async (id: string) => {
    const item = downloads.find((d) => d.id === id);
    if (item?.task_id) {
      try {
        await api.delete(`/downloads/${item.task_id}`);
      } catch {
        // Всё равно убираем из UI
      }
    }
    if (item) addLog(`⚠️ Отменено: ${item.filename}`, "warn");
    setDownloads((prev) => prev.filter((d) => d.id !== id));
  };

  // Pause/Resume — API не поддерживает, только визуальное состояние
  const handlePause = (id: string) => {
    setDownloads((prev) => prev.map((d) => (d.id === id ? { ...d, status: "paused" } : d)));
    const item = downloads.find((d) => d.id === id);
    if (item) addLog(`Приостановлено: ${item.filename}`, "warn");
  };

  const handleResume = (id: string) => {
    setDownloads((prev) => prev.map((d) => (d.id === id ? { ...d, status: "downloading" } : d)));
    const item = downloads.find((d) => d.id === id);
    if (item) addLog(`Возобновлено: ${item.filename}`, "info");
  };

  const canDownload = !!videoInfo && !downloading;

  return (
    <div className="flex flex-col gap-6 h-full">
      {/* Heading */}
      <h1
        style={{ fontSize: "32px", fontWeight: 700, color: "#FFFFFF", lineHeight: 1.2 }}
      >
        Download your favorite videos
      </h1>

      {/* URL Input */}
      <div
        className="flex items-center overflow-hidden"
        style={{
          backgroundColor: "#152019",
          border: "1px solid #04594D",
          borderRadius: "10px",
          height: "48px",
        }}
      >
        <div className="flex items-center gap-2 flex-1 px-4">
          <Search size={16} style={{ color: "#4B5563", flexShrink: 0 }} />
          <input
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAnalysis()}
            placeholder="Enter video URL..."
            className="flex-1 bg-transparent outline-none"
            style={{ color: "#FFFFFF", fontSize: "14px" }}
          />
        </div>
        <button
          onClick={handleAnalysis}
          disabled={analyzing || !url.trim()}
          className="flex items-center justify-center h-full px-6 transition-all cursor-pointer disabled:opacity-50"
          style={{
            backgroundColor: "#22C55E",
            color: "#FFFFFF",
            fontSize: "14px",
            fontWeight: 600,
            borderRadius: "0 8px 8px 0",
            minWidth: "110px",
          }}
        >
          {analyzing ? (
            <span className="flex items-center gap-2">
              <span
                className="inline-block rounded-full border-2 border-white border-t-transparent animate-spin"
                style={{ width: 14, height: 14 }}
              />
              Analyzing
            </span>
          ) : (
            "Analysis"
          )}
        </button>
      </div>

      {/* Video Info Panel — показывается после успешного анализа */}
      {videoInfo && (
        <div
          className="flex flex-col gap-4 p-4 rounded-xl"
          style={{ backgroundColor: "#152019", border: "1px solid #04594D" }}
        >
          {/* Название и превью */}
          <div className="flex items-start gap-3">
            {videoInfo.thumbnail && (
              <img
                src={videoInfo.thumbnail}
                alt="thumbnail"
                className="rounded"
                style={{ width: "80px", height: "54px", objectFit: "cover", flexShrink: 0 }}
              />
            )}
            <p style={{ color: "#FFFFFF", fontSize: "13px", fontWeight: 600, lineHeight: 1.4 }}>
              {videoInfo.title}
            </p>
          </div>

          {/* Выбор качества и опции */}
          <div className="flex items-center gap-4 flex-wrap">
            {/* Селектор качества */}
            <div className="flex items-center gap-2">
              <Film size={14} style={{ color: "#9CA3AF" }} />
              <select
                value={selectedQuality}
                onChange={(e) => {
                  setSelectedQuality(e.target.value);
                  setAudioOnly(false);
                }}
                disabled={audioOnly}
                className="rounded px-2 py-1 outline-none cursor-pointer disabled:opacity-40"
                style={{
                  backgroundColor: "#0A1210",
                  border: "1px solid #04594D",
                  color: "#FFFFFF",
                  fontSize: "12px",
                }}
              >
                {videoInfo.qualities.map((q) => (
                  <option key={q} value={q}>
                    {q}
                  </option>
                ))}
              </select>
            </div>

            {/* Audio only */}
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={audioOnly}
                onChange={(e) => setAudioOnly(e.target.checked)}
                style={{ accentColor: "#22C55E" }}
              />
              <Music size={13} style={{ color: "#9CA3AF" }} />
              <span style={{ color: "#D1D5DB", fontSize: "12px" }}>Audio only</span>
            </label>
          </div>
        </div>
      )}

      {/* Download items list */}
      {downloads.length > 0 && (
        <div className="flex flex-col gap-3">
          {downloads.map((item) => (
            <DownloadCard
              key={item.id}
              item={item}
              onPause={handlePause}
              onCancel={handleCancel}
              onResume={handleResume}
              showUser={isPrivileged}
            />
          ))}
        </div>
      )}

      {/* Download button */}
      <div className="flex justify-center">
        <button
          onClick={handleDownload}
          disabled={!canDownload}
          className="flex items-center justify-center gap-2 transition-all cursor-pointer disabled:opacity-40"
          style={{
            backgroundColor: "#22C55E",
            color: "#FFFFFF",
            fontSize: "15px",
            fontWeight: 600,
            borderRadius: "8px",
            height: "44px",
            width: "200px",
          }}
        >
          <Download size={16} />
          Download
        </button>
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Log Console */}
      <LogConsole logs={logs} />
    </div>
  );
}
