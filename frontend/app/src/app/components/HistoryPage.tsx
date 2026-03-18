import { useState, useEffect, useCallback } from "react";
import { CheckCircle, XCircle, Trash2, Download } from "lucide-react";
import api from "@/api/client";

function extractDomain(url: string) {
  try {
    const u = new URL(url.startsWith("http") ? url : "https://" + url);
    return u.hostname.replace("www.", "");
  } catch {
    return url;
  }
}

function formatBytes(bytes: number | null): string {
  if (!bytes) return "—";
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  if (bytes >= 1e3) return `${(bytes / 1e3).toFixed(0)} KB`;
  return `${bytes} B`;
}

interface HistoryItem {
  id: string;
  filename: string;
  status: "completed" | "failed";
  size: string;
  date: string;
  source: string;
  user_id: number | null;
  user_name: string | null;
}

export function HistoryPage() {
  const stored = JSON.parse(localStorage.getItem("user") || "{}");
  const currentUserRole: string = stored.role ?? "user";
  const currentUserId: number | null = stored.user_id ?? null;
  const isPrivileged = currentUserRole === "root" || currentUserRole === "admin";

  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [deleting, setDeleting] = useState<string | null>(null);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await api.get("/downloads");
      const tasks = res.data as any[];
      const done: HistoryItem[] = tasks
        .filter((t) => ["completed", "error", "cancelled"].includes(t.status))
        .map((t) => ({
          id: t.id,
          filename: t.filename || extractDomain(t.url),
          status: t.status === "completed" ? "completed" : "failed",
          size: formatBytes(t.file_size),
          date: (t.finished_at || t.created_at || "").slice(0, 10) || "—",
          source: extractDomain(t.url),
          user_id: t.user_id ?? null,
          user_name: t.user_name ?? null,
        }));
      // Сначала самые новые
      setHistory(done.reverse());
    } catch {
      // Игнорируем ошибки загрузки
    }
  }, []);

  useEffect(() => {
    fetchHistory();
  }, [fetchHistory]);

  const handleDelete = async (id: string) => {
    setDeleting(id);
    try {
      await api.delete(`/downloads/${id}`);
      setHistory((prev) => prev.filter((item) => item.id !== id));
    } catch {
      // Если ошибка — обновим список с сервера
      await fetchHistory();
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <h1 style={{ fontSize: "28px", fontWeight: 700, color: "#FFFFFF" }}>Download History</h1>

      {history.length === 0 ? (
        <div
          className="flex items-center justify-center rounded-xl"
          style={{ backgroundColor: "#152019", border: "1px solid #04594D", height: "120px" }}
        >
          <span style={{ color: "#6B7280", fontSize: "13px" }}>No completed downloads yet</span>
        </div>
      ) : (
        <div
          className="rounded-xl overflow-hidden"
          style={{ border: "1px solid #04594D" }}
        >
          <table className="w-full">
            <thead>
              <tr style={{ backgroundColor: "#0F1A14", borderBottom: "1px solid #04594D" }}>
                {(["Status", "Filename", "Size", "Source"] as const).map((h, i) => (
                  <th
                    key={i}
                    className="text-left px-4 py-3"
                    style={{ color: "#6B7280", fontSize: "11px", fontWeight: 600, letterSpacing: "0.5px" }}
                  >
                    {h.toUpperCase()}
                  </th>
                ))}
                {isPrivileged && (
                  <th
                    className="text-left px-4 py-3"
                    style={{ color: "#6B7280", fontSize: "11px", fontWeight: 600, letterSpacing: "0.5px" }}
                  >
                    ПОЛЬЗОВАТЕЛЬ
                  </th>
                )}
                {(["Date", "", ""] as const).map((h, i) => (
                  <th
                    key={i}
                    className="text-left px-4 py-3"
                    style={{ color: "#6B7280", fontSize: "11px", fontWeight: 600, letterSpacing: "0.5px" }}
                  >
                    {h.toUpperCase()}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {history.map((item, i) => {
                const canDelete = isPrivileged || item.user_id === currentUserId;
                return (
                  <tr
                    key={item.id}
                    style={{
                      backgroundColor: i % 2 === 0 ? "#152019" : "#111A15",
                      borderBottom: "1px solid #04594D",
                    }}
                  >
                    <td className="px-4 py-3">
                      {item.status === "completed" ? (
                        <CheckCircle size={14} style={{ color: "#22C55E" }} />
                      ) : (
                        <XCircle size={14} style={{ color: "#EF4444" }} />
                      )}
                    </td>
                    <td className="px-4 py-3" style={{ color: "#FFFFFF", fontSize: "13px" }}>
                      {item.filename}
                    </td>
                    <td className="px-4 py-3" style={{ color: "#9CA3AF", fontSize: "13px" }}>
                      {item.size}
                    </td>
                    <td className="px-4 py-3" style={{ color: "#22C55E", fontSize: "13px" }}>
                      {item.source}
                    </td>
                    {isPrivileged && (
                      <td className="px-4 py-3" style={{ color: "#9CA3AF", fontSize: "13px" }}>
                        {item.user_name ?? (item.user_id !== null ? String(item.user_id) : "—")}
                      </td>
                    )}
                    <td className="px-4 py-3" style={{ color: "#6B7280", fontSize: "13px" }}>
                      {item.date}
                    </td>
                    <td className="px-4 py-3">
                      {item.status === "completed" && (
                        <a
                          href={`/api/downloads/${item.id}/file`}
                          download
                          className="flex items-center justify-center rounded transition-all"
                          style={{
                            width: "28px",
                            height: "28px",
                            backgroundColor: "#0F2A1A",
                            border: "1px solid #04594D",
                            color: "#22C55E",
                            textDecoration: "none",
                          }}
                          title="Скачать файл"
                        >
                          <Download size={12} />
                        </a>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {canDelete && (
                        <button
                          onClick={() => handleDelete(item.id)}
                          disabled={deleting === item.id}
                          className="flex items-center justify-center rounded transition-all cursor-pointer disabled:opacity-40"
                          style={{
                            width: "28px",
                            height: "28px",
                            backgroundColor: "#2A1515",
                            border: "1px solid #4D2020",
                            color: "#EF4444",
                          }}
                          title="Удалить из истории"
                        >
                          <Trash2 size={12} />
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
