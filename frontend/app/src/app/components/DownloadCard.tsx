import { Pause, X, CheckCircle, AlertCircle, Loader } from "lucide-react";

export type DownloadStatus = "error" | "downloading" | "paused" | "completed" | "pending";

export interface DownloadItem {
  id: string;
  filename: string;
  status: DownloadStatus;
  timeLeft?: string;
  speed?: string;
  source?: string;
  progress?: number;
  /** ID задачи в FastAPI (появляется после POST /downloads) */
  task_id?: string;
  /** Оригинальный URL для создания задачи скачивания */
  originalUrl?: string;
  user_id?: number | null;
  user_name?: string | null;
}

interface DownloadCardProps {
  item: DownloadItem;
  onPause: (id: string) => void;
  onCancel: (id: string) => void;
  onResume: (id: string) => void;
  /** Показывать блок «User» (только для root/admin) */
  showUser?: boolean;
}

const statusColors: Record<DownloadStatus, string> = {
  error: "#EF4444",
  downloading: "#22C55E",
  paused: "#F59E0B",
  completed: "#22C55E",
  pending: "#6B7280",
};

const statusLabels: Record<DownloadStatus, string> = {
  error: "Error",
  downloading: "Downloading",
  paused: "Paused",
  completed: "Completed",
  pending: "Pending",
};

export function DownloadCard({ item, onPause, onCancel, onResume, showUser }: DownloadCardProps) {
  return (
    <div
      className="flex items-center justify-between px-4 py-3 rounded-lg"
      style={{
        backgroundColor: "#152019",
        border: "1px solid #04594D",
      }}
    >
      {/* Left: file info */}
      <div className="flex flex-col gap-1 min-w-0">
        <div className="flex items-center gap-2">
          {item.status === "downloading" && <Loader size={13} style={{ color: "#22C55E" }} className="animate-spin" />}
          {item.status === "completed" && <CheckCircle size={13} style={{ color: "#22C55E" }} />}
          {item.status === "error" && <AlertCircle size={13} style={{ color: "#EF4444" }} />}
          <span
            style={{ color: "#FFFFFF", fontSize: "13px", fontWeight: 600 }}
            className="truncate"
          >
            {item.filename}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <span style={{ color: "#6B7280", fontSize: "11px" }}>Status</span>
          <span style={{ color: statusColors[item.status], fontSize: "11px", fontWeight: 600 }}>
            {statusLabels[item.status]}
          </span>
        </div>
        {item.progress !== undefined && item.status === "downloading" && (
          <div
            className="rounded-full overflow-hidden mt-1"
            style={{ height: "3px", width: "160px", backgroundColor: "#1E3328" }}
          >
            <div
              className="h-full rounded-full transition-all"
              style={{ width: `${item.progress}%`, backgroundColor: "#22C55E" }}
            />
          </div>
        )}
      </div>

      {/* Right: stats + buttons */}
      <div className="flex items-center gap-4">
        {(item.timeLeft || item.speed || item.source) && (
          <div className="flex flex-col gap-0.5 text-right">
            {item.timeLeft && (
              <span style={{ color: "#9CA3AF", fontSize: "11px" }}>
                Time Left: <span style={{ color: "#D1D5DB" }}>{item.timeLeft}</span>
              </span>
            )}
            {item.speed && (
              <span style={{ color: "#9CA3AF", fontSize: "11px" }}>
                Speed: <span style={{ color: "#D1D5DB" }}>{item.speed}</span>
              </span>
            )}
            {item.source && (
              <span style={{ color: "#9CA3AF", fontSize: "11px" }}>
                Source:{" "}
                <span style={{ color: "#22C55E", textDecoration: "underline", cursor: "pointer" }}>
                  {item.source}
                </span>
              </span>
            )}
            {showUser && (
              <span style={{ color: "#9CA3AF", fontSize: "11px" }}>
                User:{" "}
                <span style={{ color: item.user_id != null ? "#D1D5DB" : "#6B7280" }}>
                  {item.user_id != null ? (item.user_name ?? String(item.user_id)) : "—"}
                </span>
              </span>
            )}
          </div>
        )}

        <div className="flex items-center gap-2">
          {item.status !== "completed" && item.status !== "error" && (
            <button
              onClick={() =>
                item.status === "paused" ? onResume(item.id) : onPause(item.id)
              }
              className="flex items-center justify-center rounded transition-all cursor-pointer"
              style={{
                height: "30px",
                paddingLeft: "10px",
                paddingRight: "10px",
                backgroundColor: "#1E3328",
                border: "1px solid #04594D",
                color: "#D1D5DB",
                fontSize: "12px",
              }}
            >
              {item.status === "paused" ? "Resume" : (
                <>
                  <Pause size={12} className="mr-1" />
                  Pause
                </>
              )}
            </button>
          )}
          <button
            onClick={() => onCancel(item.id)}
            className="flex items-center justify-center rounded transition-all cursor-pointer"
            style={{
              height: "30px",
              paddingLeft: "10px",
              paddingRight: "10px",
              backgroundColor: "#2A1515",
              border: "1px solid #4D2020",
              color: "#EF4444",
              fontSize: "12px",
            }}
          >
            <X size={12} className="mr-1" />
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}