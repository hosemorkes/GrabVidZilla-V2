import { useState, useEffect } from "react";
import { Loader } from "lucide-react";
import api from "@/api/client";

function extractDomain(url: string) {
  try {
    const u = new URL(url.startsWith("http") ? url : "https://" + url);
    return u.hostname.replace("www.", "");
  } catch {
    return url;
  }
}

interface ActiveTask {
  id: string;
  filename: string | null;
  progress: number;
  speed: string | null;
  timeLeft: string | null;
  source: string;
  user_id: number | null;
  user_name: string | null;
}

export function ActiveDownloadsPage() {
  const stored = JSON.parse(localStorage.getItem("user") || "{}");
  const isPrivileged = stored.role === "root" || stored.role === "admin";

  const [activeDownloads, setActiveDownloads] = useState<ActiveTask[]>([]);

  useEffect(() => {
    // Загружаем активные задачи из API и обновляем каждые 2 секунды
    const fetchActive = async () => {
      try {
        const res = await api.get("/downloads");
        const tasks = res.data as any[];
        const active: ActiveTask[] = tasks
          .filter((t) => t.status === "downloading" || t.status === "queued")
          .map((t) => ({
            id: t.id,
            filename: t.filename || extractDomain(t.url),
            progress: t.progress ?? 0,
            speed: t.speed || null,
            timeLeft: t.eta || null,
            source: extractDomain(t.url),
            user_id: t.user_id ?? null,
            user_name: t.user_name ?? null,
          }));
        setActiveDownloads(active);
      } catch {
        // Игнорируем ошибки — попробуем на следующей итерации
      }
    };

    fetchActive();
    const interval = setInterval(fetchActive, 2000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="flex flex-col gap-6">
      <h1 style={{ fontSize: "28px", fontWeight: 700, color: "#FFFFFF" }}>Active Downloads</h1>

      {activeDownloads.length === 0 ? (
        <div
          className="flex items-center justify-center rounded-xl"
          style={{ backgroundColor: "#152019", border: "1px solid #04594D", height: "120px" }}
        >
          <span style={{ color: "#6B7280", fontSize: "13px" }}>No active downloads</span>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {activeDownloads.map((item) => (
            <div
              key={item.id}
              className="flex flex-col gap-3 px-4 py-4 rounded-xl"
              style={{ backgroundColor: "#152019", border: "1px solid #04594D" }}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Loader size={14} style={{ color: "#22C55E" }} className="animate-spin" />
                  <span style={{ color: "#FFFFFF", fontSize: "13px", fontWeight: 600 }}>
                    {item.filename}
                  </span>
                </div>
                <div className="flex gap-4">
                  {item.speed && (
                    <span style={{ color: "#9CA3AF", fontSize: "11px" }}>
                      Speed: <span style={{ color: "#D1D5DB" }}>{item.speed}</span>
                    </span>
                  )}
                  {item.timeLeft && (
                    <span style={{ color: "#9CA3AF", fontSize: "11px" }}>
                      ETA: <span style={{ color: "#D1D5DB" }}>{item.timeLeft}</span>
                    </span>
                  )}
                  <span style={{ color: "#9CA3AF", fontSize: "11px" }}>
                    Source: <span style={{ color: "#22C55E" }}>{item.source}</span>
                  </span>
                  {isPrivileged && (
                    <span style={{ color: "#9CA3AF", fontSize: "11px" }}>
                      User:{" "}
                      <span style={{ color: item.user_id != null ? "#D1D5DB" : "#6B7280" }}>
                        {item.user_id != null ? (item.user_name ?? String(item.user_id)) : "—"}
                      </span>
                    </span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-3">
                <div
                  className="flex-1 rounded-full overflow-hidden"
                  style={{ height: "4px", backgroundColor: "#1E3328" }}
                >
                  <div
                    className="h-full rounded-full"
                    style={{ width: `${item.progress}%`, backgroundColor: "#22C55E" }}
                  />
                </div>
                <span style={{ color: "#22C55E", fontSize: "12px", fontWeight: 600 }}>
                  {Math.round(item.progress)}%
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
