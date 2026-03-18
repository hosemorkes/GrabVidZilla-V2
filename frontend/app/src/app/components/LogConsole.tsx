/**
 * LogConsole.tsx — два экспорта:
 *
 * 1. LogConsole — мини-терминал для DownloadsPage (без изменений).
 * 2. LogPage   — полная страница системных логов для роута /log.
 */

import { useState, useEffect, useCallback } from "react";
import { RefreshCw, Trash2, ChevronLeft, ChevronRight } from "lucide-react";
import api from "@/api/client";

// ─────────────────────────────────────────────────────────────────────────────
// LogConsole — мини-терминал (используется в DownloadsPage)
// ─────────────────────────────────────────────────────────────────────────────

interface LogEntry {
  timestamp: string;
  message: string;
  type: "info" | "error" | "success" | "warn";
}

interface LogConsoleProps {
  logs: LogEntry[];
}

const logColors: Record<LogEntry["type"], string> = {
  info: "#9CA3AF",
  error: "#EF4444",
  success: "#22C55E",
  warn: "#F59E0B",
};

const logPrefixes: Record<LogEntry["type"], string> = {
  info: "[INFO]",
  error: "[ERROR]",
  success: "[OK]",
  warn: "[WARN]",
};

export type { LogEntry };

export function LogConsole({ logs }: LogConsoleProps) {
  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{
        backgroundColor: "#0A1210",
        border: "1px solid #04594D",
        minHeight: "220px",
      }}
    >
      <div
        className="flex items-center gap-2 px-4 py-2"
        style={{ borderBottom: "1px solid #04594D" }}
      >
        <div className="flex gap-1.5">
          <div className="rounded-full" style={{ width: 8, height: 8, backgroundColor: "#EF4444" }} />
          <div className="rounded-full" style={{ width: 8, height: 8, backgroundColor: "#F59E0B" }} />
          <div className="rounded-full" style={{ width: 8, height: 8, backgroundColor: "#22C55E" }} />
        </div>
        <span style={{ color: "#6B7280", fontSize: "11px" }}>Console Log</span>
      </div>
      <div className="p-3 overflow-y-auto" style={{ maxHeight: "260px", minHeight: "160px" }}>
        {logs.length === 0 ? (
          <span style={{ color: "#374151", fontSize: "11px" }}>No log entries...</span>
        ) : (
          <div className="flex flex-col gap-0.5">
            {logs.map((log, index) => (
              <div key={index} className="flex gap-2">
                <span style={{ color: "#374151", fontSize: "11px", flexShrink: 0 }}>
                  {log.timestamp}
                </span>
                <span
                  style={{
                    color: logColors[log.type],
                    fontSize: "11px",
                    flexShrink: 0,
                    fontWeight: 600,
                  }}
                >
                  {logPrefixes[log.type]}
                </span>
                <span style={{ color: "#9CA3AF", fontSize: "11px" }}>{log.message}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// LogPage — полная страница системных логов (/log)
// ─────────────────────────────────────────────────────────────────────────────

interface SystemLogItem {
  id: number;
  created_at: string;
  level: string;
  source: string;
  event_type: string;
  user_id: number | null;
  task_id: number | null;
  message: string;
  details: string | null;
  ip_address: string | null;
}

const PAGE_SIZE = 50;

// Цвета уровней логирования
const LEVEL_STYLES: Record<string, { bg: string; color: string }> = {
  INFO:     { bg: "#0F1E3D", color: "#60A5FA" },
  WARNING:  { bg: "#2D1F00", color: "#F59E0B" },
  ERROR:    { bg: "#2D0808", color: "#EF4444" },
  CRITICAL: { bg: "#1A0000", color: "#B91C1C" },
};

// Цвета источников
const SOURCE_STYLES: Record<string, { bg: string; color: string }> = {
  api:    { bg: "#0A1E1A", color: "#1faa89" },
  worker: { bg: "#1A0E2D", color: "#A78BFA" },
  bot:    { bg: "#0A1630", color: "#60A5FA" },
  cli:    { bg: "#1F1000", color: "#FB923C" },
};

/** Форматирует ISO-дату в локальное время браузера.
 *
 * API хранит время в UTC без суффикса (напр. "2024-01-15T12:30:45").
 * Чтобы JS знал, что это UTC, добавляем "Z" если суффикс отсутствует —
 * тогда toLocaleString() автоматически переведёт в часовой пояс пользователя.
 */
function formatDateTime(iso: string): string {
  try {
    // Нормализуем пробел → T (формат SQLite) и добавляем Z если нет зоны
    const normalized = /[Zz+\-]\d{2}:\d{2}$|[Zz]$/.test(iso)
      ? iso
      : iso.replace(" ", "T") + "Z";
    return new Date(normalized).toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

/** Цветной бейдж уровня (INFO / WARNING / ERROR / CRITICAL) */
function LevelBadge({ level }: { level: string }) {
  const s = LEVEL_STYLES[level] ?? { bg: "#152019", color: "#9CA3AF" };
  return (
    <span
      style={{
        backgroundColor: s.bg,
        color: s.color,
        fontSize: "10px",
        fontWeight: 700,
        padding: "2px 6px",
        borderRadius: "4px",
        letterSpacing: "0.4px",
        whiteSpace: "nowrap",
      }}
    >
      {level}
    </span>
  );
}

/** Цветной бейдж источника (api / worker / bot / cli) */
function SourceBadge({ source }: { source: string }) {
  const s = SOURCE_STYLES[source] ?? { bg: "#152019", color: "#9CA3AF" };
  return (
    <span
      style={{
        backgroundColor: s.bg,
        color: s.color,
        fontSize: "10px",
        fontWeight: 600,
        padding: "2px 6px",
        borderRadius: "4px",
        whiteSpace: "nowrap",
      }}
    >
      {source}
    </span>
  );
}

// Общий стиль для <select> и <input> в панели фильтров
const filterInputStyle: React.CSSProperties = {
  backgroundColor: "#0A1210",
  border: "1px solid #04594D",
  color: "#FFFFFF",
  fontSize: "12px",
  borderRadius: "6px",
  padding: "5px 8px",
  outline: "none",
};

// ─── Основной компонент ───────────────────────────────────────────────────────

export function LogPage() {
  const [items, setItems] = useState<SystemLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [cleaningUp, setCleaningUp] = useState(false);
  const [selectedItem, setSelectedItem] = useState<SystemLogItem | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);

  // Фильтры
  const [filterSource, setFilterSource] = useState("");
  const [filterLevel, setFilterLevel] = useState("");
  const [filterEventType, setFilterEventType] = useState("");
  const [filterDateFrom, setFilterDateFrom] = useState("");
  const [filterDateTo, setFilterDateTo] = useState("");
  const [filterUserId, setFilterUserId] = useState("");

  // Текущий пользователь из localStorage
  const userRaw = localStorage.getItem("user");
  const currentUser = userRaw ? JSON.parse(userRaw) : null;
  const role: string = currentUser?.role ?? "";
  const isAdmin = role === "root" || role === "admin";
  const isRoot = role === "root";

  // ── Загрузка логов ────────────────────────────────────────────────────────

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      const params: Record<string, string | number> = {
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        caller_role: role,
      };
      if (currentUser?.user_id) params.caller_user_id = currentUser.user_id;
      if (filterSource)    params.source     = filterSource;
      if (filterLevel)     params.level      = filterLevel;
      if (filterEventType) params.event_type = filterEventType;
      if (filterDateFrom)  params.date_from  = filterDateFrom;
      if (filterDateTo)    params.date_to    = filterDateTo;
      if (isRoot && filterUserId.trim()) params.user_id = filterUserId.trim();

      const res = await api.get("/logs", { params });
      setItems(res.data.items ?? []);
      setTotal(res.data.total ?? 0);
    } catch (err: any) {
      const msg = err.response?.data?.detail ?? err.message ?? "Ошибка загрузки логов";
      setFetchError(msg);
      setItems([]);
    } finally {
      setLoading(false);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, role, filterSource, filterLevel, filterEventType, filterDateFrom, filterDateTo, filterUserId]);

  // Загрузка при монтировании и изменении зависимостей
  useEffect(() => {
    fetchLogs();
  }, [fetchLogs]);

  // Авто-обновление каждые 30 секунд
  useEffect(() => {
    const interval = setInterval(fetchLogs, 30_000);
    return () => clearInterval(interval);
  }, [fetchLogs]);

  // Обёртка: при изменении фильтра сбрасываем страницу на 0
  const setFilter = (setter: (v: string) => void) => (v: string) => {
    setter(v);
    setPage(0);
  };

  // ── Очистка старых логов ──────────────────────────────────────────────────

  const handleCleanup = async () => {
    if (
      !window.confirm(
        "Удалить старые логи согласно настройкам хранения (LOG_RETENTION_DAYS / MAX_LOG_ROWS)?",
      )
    )
      return;
    setCleaningUp(true);
    try {
      await api.delete("/logs/cleanup");
      await fetchLogs();
    } catch (err: any) {
      const msg = err.response?.data?.detail ?? err.message ?? "Ошибка очистки";
      alert(`Ошибка: ${msg}`);
    } finally {
      setCleaningUp(false);
    }
  };

  // ── Формирование JSON для модалки ─────────────────────────────────────────

  const getDetailsJson = (item: SystemLogItem): string => {
    const base: Record<string, unknown> = {
      id: item.id,
      created_at: item.created_at,
      level: item.level,
      source: item.source,
      event_type: item.event_type,
      user_id: item.user_id,
      task_id: item.task_id,
      message: item.message,
      ip_address: item.ip_address,
      details: null as unknown,
    };
    if (item.details) {
      try {
        base.details = JSON.parse(item.details);
      } catch {
        base.details = item.details;
      }
    }
    return JSON.stringify(base, null, 2);
  };

  // ── Вычисляемые значения ──────────────────────────────────────────────────

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const showUserCol = isAdmin;

  // Заголовки колонок (колонка «Пользователь» — только для admin/root)
  const colHeaders = showUserCol
    ? ["Дата / Время", "Level", "Source", "Event Type", "Пользователь", "Сообщение", ""]
    : ["Дата / Время", "Level", "Source", "Event Type", "Сообщение", ""];

  const COL_WIDTHS: Record<string, string> = {
    "Дата / Время": "148px",
    "Level":        "76px",
    "Source":       "66px",
    "Event Type":   "160px",
    "Пользователь": "80px",
    "":             "38px",
  };

  // ── Разметка ──────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-5">

      {/* ── Заголовок страницы ── */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 style={{ fontSize: "28px", fontWeight: 700, color: "#FFFFFF" }}>
          System Logs
        </h1>
        <div className="flex items-center gap-2">
          {/* Кнопка «Очистить старые» — только для root */}
          {isRoot && (
            <button
              onClick={handleCleanup}
              disabled={cleaningUp}
              className="flex items-center gap-1.5 cursor-pointer disabled:opacity-40 transition-all"
              style={{
                backgroundColor: "#2A1515",
                border: "1px solid #4D2020",
                color: "#EF4444",
                fontSize: "12px",
                fontWeight: 600,
                padding: "6px 12px",
                borderRadius: "6px",
              }}
            >
              <Trash2 size={13} />
              {cleaningUp ? "Очищаю..." : "Очистить старые"}
            </button>
          )}
          {/* Кнопка «Обновить» */}
          <button
            onClick={() => fetchLogs()}
            disabled={loading}
            className="flex items-center gap-1.5 cursor-pointer disabled:opacity-40 transition-all"
            style={{
              backgroundColor: "#0F2A24",
              border: "1px solid #04594D",
              color: "#1faa89",
              fontSize: "12px",
              fontWeight: 600,
              padding: "6px 12px",
              borderRadius: "6px",
            }}
          >
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
            Обновить
          </button>
        </div>
      </div>

      {/* ── Панель фильтров (только для admin / root) ── */}
      {isAdmin && (
        <div
          className="flex flex-wrap gap-x-4 gap-y-3 p-4 rounded-xl"
          style={{ backgroundColor: "#0A1210", border: "1px solid #04594D" }}
        >
          {/* Source */}
          <div className="flex flex-col gap-1">
            <span style={{ color: "#6B7280", fontSize: "10px", fontWeight: 600, letterSpacing: "0.5px" }}>
              SOURCE
            </span>
            <select
              value={filterSource}
              onChange={(e) => setFilter(setFilterSource)(e.target.value)}
              style={filterInputStyle}
            >
              <option value="">Все</option>
              <option value="api">api</option>
              <option value="worker">worker</option>
              <option value="bot">bot</option>
              <option value="cli">cli</option>
            </select>
          </div>

          {/* Level */}
          <div className="flex flex-col gap-1">
            <span style={{ color: "#6B7280", fontSize: "10px", fontWeight: 600, letterSpacing: "0.5px" }}>
              LEVEL
            </span>
            <select
              value={filterLevel}
              onChange={(e) => setFilter(setFilterLevel)(e.target.value)}
              style={filterInputStyle}
            >
              <option value="">Все</option>
              <option value="INFO">INFO</option>
              <option value="WARNING">WARNING</option>
              <option value="ERROR">ERROR</option>
              <option value="CRITICAL">CRITICAL</option>
            </select>
          </div>

          {/* Event Type */}
          <div className="flex flex-col gap-1">
            <span style={{ color: "#6B7280", fontSize: "10px", fontWeight: 600, letterSpacing: "0.5px" }}>
              EVENT TYPE
            </span>
            <select
              value={filterEventType}
              onChange={(e) => setFilter(setFilterEventType)(e.target.value)}
              style={filterInputStyle}
            >
              <option value="">Все</option>
              <optgroup label="Скачивание">
                <option value="download_started">download_started</option>
                <option value="download_completed">download_completed</option>
                <option value="download_failed">download_failed</option>
                <option value="download_cancelled">download_cancelled</option>
              </optgroup>
              <optgroup label="Конвертация">
                <option value="convert_started">convert_started</option>
                <option value="convert_completed">convert_completed</option>
                <option value="convert_failed">convert_failed</option>
              </optgroup>
              <optgroup label="Аутентификация">
                <option value="login">login</option>
                <option value="login_failed">login_failed</option>
                <option value="logout">logout</option>
                <option value="user_created">user_created</option>
                <option value="user_deactivated">user_deactivated</option>
              </optgroup>
              <optgroup label="Система">
                <option value="system_startup">system_startup</option>
                <option value="system_shutdown">system_shutdown</option>
                <option value="cleanup_ran">cleanup_ran</option>
              </optgroup>
            </select>
          </div>

          {/* Date From */}
          <div className="flex flex-col gap-1">
            <span style={{ color: "#6B7280", fontSize: "10px", fontWeight: 600, letterSpacing: "0.5px" }}>
              DATE FROM
            </span>
            <input
              type="date"
              value={filterDateFrom}
              onChange={(e) => setFilter(setFilterDateFrom)(e.target.value)}
              style={{ ...filterInputStyle, colorScheme: "dark" }}
            />
          </div>

          {/* Date To */}
          <div className="flex flex-col gap-1">
            <span style={{ color: "#6B7280", fontSize: "10px", fontWeight: 600, letterSpacing: "0.5px" }}>
              DATE TO
            </span>
            <input
              type="date"
              value={filterDateTo}
              onChange={(e) => setFilter(setFilterDateTo)(e.target.value)}
              style={{ ...filterInputStyle, colorScheme: "dark" }}
            />
          </div>

          {/* User ID — только для root */}
          {isRoot && (
            <div className="flex flex-col gap-1">
              <span style={{ color: "#6B7280", fontSize: "10px", fontWeight: 600, letterSpacing: "0.5px" }}>
                USER ID
              </span>
              <input
                type="text"
                placeholder="123"
                value={filterUserId}
                onChange={(e) => setFilter(setFilterUserId)(e.target.value)}
                style={{ ...filterInputStyle, width: "72px" }}
              />
            </div>
          )}

          {/* Кнопка «Сбросить» — показывается, если есть активные фильтры */}
          {(filterSource || filterLevel || filterEventType || filterDateFrom || filterDateTo || filterUserId) && (
            <div className="flex flex-col justify-end">
              <button
                onClick={() => {
                  setFilterSource("");
                  setFilterLevel("");
                  setFilterEventType("");
                  setFilterDateFrom("");
                  setFilterDateTo("");
                  setFilterUserId("");
                  setPage(0);
                }}
                className="cursor-pointer transition-all"
                style={{
                  backgroundColor: "transparent",
                  border: "1px solid #374151",
                  color: "#9CA3AF",
                  fontSize: "11px",
                  padding: "5px 10px",
                  borderRadius: "6px",
                }}
              >
                Сбросить
              </button>
            </div>
          )}
        </div>
      )}

      {/* ── Блок ошибки ── */}
      {fetchError && (
        <div
          className="px-4 py-3 rounded-lg"
          style={{
            backgroundColor: "#2D0808",
            border: "1px solid #4D2020",
            color: "#EF4444",
            fontSize: "13px",
          }}
        >
          ⚠️ {fetchError}
        </div>
      )}

      {/* ── Таблица логов ── */}
      {items.length === 0 && !loading ? (
        <div
          className="flex items-center justify-center rounded-xl"
          style={{ backgroundColor: "#0A1210", border: "1px solid #04594D", height: "120px" }}
        >
          <span style={{ color: "#6B7280", fontSize: "13px" }}>
            {fetchError ? "Не удалось загрузить логи" : "Нет записей"}
          </span>
        </div>
      ) : (
        <div className="rounded-xl overflow-hidden" style={{ border: "1px solid #04594D" }}>
          <table className="w-full" style={{ tableLayout: "fixed", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ backgroundColor: "#0F1A14", borderBottom: "1px solid #04594D" }}>
                {colHeaders.map((h, i) => (
                  <th
                    key={i}
                    className="text-left px-3 py-3"
                    style={{
                      color: "#6B7280",
                      fontSize: "10px",
                      fontWeight: 600,
                      letterSpacing: "0.5px",
                      width: COL_WIDTHS[h] ?? "auto",
                    }}
                  >
                    {h.toUpperCase()}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading && items.length === 0 ? (
                <tr>
                  <td
                    colSpan={colHeaders.length}
                    className="px-4 py-8 text-center"
                    style={{ color: "#6B7280", fontSize: "13px" }}
                  >
                    Загрузка...
                  </td>
                </tr>
              ) : (
                items.map((item, i) => (
                  <tr
                    key={item.id}
                    style={{
                      backgroundColor: i % 2 === 0 ? "#0A1210" : "#0C1410",
                      borderBottom: "1px solid #04594D22",
                    }}
                  >
                    {/* Дата / Время */}
                    <td className="px-3 py-2" style={{ color: "#6B7280", fontSize: "11px", whiteSpace: "nowrap" }}>
                      {formatDateTime(item.created_at)}
                    </td>

                    {/* Level */}
                    <td className="px-3 py-2">
                      <LevelBadge level={item.level} />
                    </td>

                    {/* Source */}
                    <td className="px-3 py-2">
                      <SourceBadge source={item.source} />
                    </td>

                    {/* Event Type */}
                    <td className="px-3 py-2" style={{ color: "#9CA3AF", fontSize: "11px" }}>
                      {item.event_type}
                    </td>

                    {/* Пользователь (admin / root) */}
                    {showUserCol && (
                      <td className="px-3 py-2" style={{ color: "#1faa89", fontSize: "11px" }}>
                        {item.user_id ?? "—"}
                      </td>
                    )}

                    {/* Сообщение (с тултипом для длинных строк) */}
                    <td className="px-3 py-2" style={{ overflow: "hidden" }}>
                      <span
                        title={item.message}
                        style={{
                          color: "#D1D5DB",
                          fontSize: "11px",
                          display: "block",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {item.message}
                      </span>
                    </td>

                    {/* Кнопка «···» — открыть детали */}
                    <td className="px-3 py-2">
                      <button
                        onClick={() => setSelectedItem(item)}
                        title="Показать детали"
                        className="flex items-center justify-center rounded cursor-pointer transition-all"
                        style={{
                          width: "26px",
                          height: "26px",
                          backgroundColor: "#152019",
                          border: "1px solid #04594D",
                          color: "#9CA3AF",
                          fontSize: "13px",
                          lineHeight: 1,
                        }}
                      >
                        ···
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Пагинация ── */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <span style={{ color: "#6B7280", fontSize: "12px" }}>
          {total > 0
            ? `Показано ${page * PAGE_SIZE + 1}–${Math.min((page + 1) * PAGE_SIZE, total)} из ${total}`
            : "Нет записей"}
        </span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0 || loading}
            className="flex items-center gap-1 cursor-pointer disabled:opacity-40 transition-all"
            style={{
              backgroundColor: "#0F2A24",
              border: "1px solid #04594D",
              color: "#FFFFFF",
              fontSize: "12px",
              fontWeight: 600,
              padding: "5px 10px",
              borderRadius: "6px",
            }}
          >
            <ChevronLeft size={14} />
            Назад
          </button>
          <span
            style={{ color: "#9CA3AF", fontSize: "12px", minWidth: "80px", textAlign: "center" }}
          >
            стр. {page + 1} / {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1 || loading}
            className="flex items-center gap-1 cursor-pointer disabled:opacity-40 transition-all"
            style={{
              backgroundColor: "#0F2A24",
              border: "1px solid #04594D",
              color: "#FFFFFF",
              fontSize: "12px",
              fontWeight: 600,
              padding: "5px 10px",
              borderRadius: "6px",
            }}
          >
            Вперёд
            <ChevronRight size={14} />
          </button>
        </div>
      </div>

      {/* ── Модалка с деталями события ── */}
      {selectedItem && (
        <div
          className="fixed inset-0 flex items-center justify-center z-50"
          style={{ backgroundColor: "rgba(0, 0, 0, 0.75)" }}
          onClick={() => setSelectedItem(null)}
        >
          <div
            className="flex flex-col rounded-xl overflow-hidden"
            style={{
              backgroundColor: "#0A1210",
              border: "1px solid #04594D",
              maxWidth: "640px",
              width: "90vw",
              maxHeight: "80vh",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Шапка модалки */}
            <div
              className="flex items-center justify-between px-4 py-3 flex-shrink-0"
              style={{ borderBottom: "1px solid #04594D" }}
            >
              <div className="flex items-center gap-2 flex-wrap">
                <LevelBadge level={selectedItem.level} />
                <SourceBadge source={selectedItem.source} />
                <span style={{ color: "#9CA3AF", fontSize: "12px" }}>
                  {selectedItem.event_type}
                </span>
                <span style={{ color: "#374151", fontSize: "11px" }}>
                  #{selectedItem.id}
                </span>
              </div>
              <button
                onClick={() => setSelectedItem(null)}
                className="cursor-pointer transition-all flex-shrink-0"
                style={{
                  color: "#6B7280",
                  fontSize: "22px",
                  lineHeight: 1,
                  background: "none",
                  border: "none",
                  marginLeft: "12px",
                }}
              >
                ×
              </button>
            </div>

            {/* JSON-содержимое */}
            <div className="overflow-y-auto flex-1 p-4">
              <pre
                style={{
                  color: "#22C55E",
                  fontSize: "12px",
                  lineHeight: 1.7,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                  margin: 0,
                }}
              >
                {getDetailsJson(selectedItem)}
              </pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
