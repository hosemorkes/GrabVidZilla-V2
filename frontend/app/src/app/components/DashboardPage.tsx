import {
  Download, CheckCircle, XCircle, Play, Trash2, HardDrive, FileText,
  Activity, Wifi, WifiOff, Loader2,
} from "lucide-react";
import {
  LineChart, Line, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import { useState, useEffect } from "react";
import api from "@/api/client";

// Цвета источников (совпадают с бэкендом)
const SOURCE_COLORS: Record<string, string> = {
  ui: "#1faa89",
  cli: "#22c55e",
  bot: "#3b82f6",
  api: "#8b5cf6",
  unknown: "#6b7280",
};

function formatBytes(bytes: number): string {
  if (bytes >= 1e12) return `${(bytes / 1e12).toFixed(1)} ТБ`;
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} ГБ`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} МБ`;
  return `${bytes} Б`;
}

// Заглушки при загрузке / отсутствии данных
const EMPTY_KPI = { total: 0, completed: 0, errors: 0, active: 0, total_bytes: 0, avg_file_size: 0, cancelled: 0 };

const CustomTooltip = ({ active, payload }: any) => {
  if (active && payload && payload.length) {
    return (
      <div
        style={{
          backgroundColor: "#071b19",
          border: "1px solid #1faa89",
          padding: "8px 12px",
          borderRadius: "6px",
        }}
      >
        <p style={{ color: "#e9fff7", fontSize: "12px", margin: 0 }}>{payload[0].payload.date}</p>
        {payload.map((entry: any, index: number) => (
          <p key={index} style={{ color: entry.color, fontSize: "11px", margin: "4px 0 0 0" }}>
            {entry.name}: {entry.value}
          </p>
        ))}
      </div>
    );
  }
  return null;
};

export function DashboardPage() {
  const stored = JSON.parse(localStorage.getItem("user") || "{}");
  const currentUserRole: string = stored.role ?? "user";
  const isPrivileged = currentUserRole === "root" || currentUserRole === "admin";

  const [kpi, setKpi] = useState(EMPTY_KPI);
  const [activityData, setActivityData] = useState<any[]>([]);
  const [topSites, setTopSites] = useState<{ domain: string; count: number }[]>([]);
  const [sources, setSources] = useState<{ name: string; count: number; color: string }[]>([]);
  const [userActivity, setUserActivity] = useState<{ userId: string; downloads: number }[]>([]);
  const [apiOnline, setApiOnline] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchStats = async () => {
      // Проверяем доступность API
      try {
        await api.get("/health");
        setApiOnline(true);
      } catch {
        setApiOnline(false);
      }

      // Загружаем агрегированную статистику
      try {
        const res = await api.get("/stats", { params: isPrivileged ? {} : { scope: "my" } });
        const s = res.data;

        setKpi({
          total: s.total,
          completed: s.completed,
          errors: s.errors,
          active: s.active,
          total_bytes: s.total_bytes,
          avg_file_size: Math.floor(s.avg_file_size),
          cancelled: s.cancelled,
        });

        // График активности: daily → [{date, tasks, mb}]
        if (s.daily?.length) {
          setActivityData(
            s.daily.map((d: any) => ({
              date: d.date.slice(5), // оставляем MM-DD
              tasks: d.count,
              mb: Math.floor(d.bytes / 1e6),
            }))
          );
        }

        // Топ сайтов
        if (s.top_domains?.length) {
          setTopSites(s.top_domains.map((d: any) => ({ domain: d.domain, count: d.count })));
        }

        // По источнику
        if (s.by_source) {
          setSources(
            Object.entries(s.by_source).map(([name, count]) => ({
              name,
              count: count as number,
              color: SOURCE_COLORS[name] ?? "#9ca3af",
            }))
          );
        }

        // По пользователям
        if (s.by_user?.length) {
          setUserActivity(
            s.by_user.map((u: any) => ({
              userId: String(u.user_id ?? "unknown"),
              downloads: u.count,
            }))
          );
        }
      } catch {
        // Если API недоступен — оставляем нули
      } finally {
        setLoading(false);
      }
    };

    fetchStats();
    // Обновляем статистику каждые 30 секунд
    const interval = setInterval(fetchStats, 30_000);
    return () => clearInterval(interval);
  }, []);

  // KPI карточки строятся из реальных данных
  const kpiStats = [
    { label: "Всего", value: kpi.total.toLocaleString(), icon: FileText, color: "#1faa89" },
    { label: "Успешно", value: kpi.completed.toLocaleString(), icon: CheckCircle, color: "#1faa89" },
    { label: "Ошибки", value: kpi.errors.toLocaleString(), icon: XCircle, color: "#ef4444" },
    { label: "Активные", value: kpi.active.toLocaleString(), icon: Activity, color: "#f59e0b" },
    { label: "Общий размер", value: formatBytes(kpi.total_bytes), icon: HardDrive, color: "#1faa89" },
    { label: "Ср. размер файла", value: formatBytes(kpi.avg_file_size), icon: Download, color: "#9ca3af" },
    { label: "Отменено", value: kpi.cancelled.toLocaleString(), icon: Trash2, color: "#6b7280" },
  ];

  // Системный статус: API через /health, Worker/Bot — косвенно через kpi.active
  const systemStatus = [
    {
      name: "API",
      status: apiOnline ? "online" : apiOnline === false ? "offline" : "checking",
      info: apiOnline ? "API отвечает" : apiOnline === false ? "API недоступен" : "Проверка...",
      icon: apiOnline ? Wifi : WifiOff,
      color: apiOnline ? "#1faa89" : "#ef4444",
    },
    {
      name: "Worker",
      status: kpi.active > 0 ? "active" : "idle",
      info: kpi.active > 0 ? `${kpi.active} задач активно` : "Нет активных задач",
      icon: Play,
      color: kpi.active > 0 ? "#f59e0b" : "#6b7280",
    },
    {
      name: "Telegram Bot",
      status: "unknown",
      info: "Статус недоступен через API",
      icon: Activity,
      color: "#6b7280",
    },
  ];

  return (
    <div className="flex flex-col gap-6 pb-8">
      <div className="flex items-center justify-between">
        <h1 style={{ fontSize: "28px", fontWeight: 700, color: "#e9fff7" }}>
          {isPrivileged ? "Статистика проекта" : "Моя статистика"}
        </h1>
        {loading && (
          <Loader2 size={16} style={{ color: "#1faa89" }} className="animate-spin" />
        )}
      </div>

      {/* Блок 1 — KPI карточки */}
      <div className="grid grid-cols-7 gap-3">
        {kpiStats.map((stat) => {
          const Icon = stat.icon;
          return (
            <div
              key={stat.label}
              className="flex flex-col gap-2 p-4 rounded-lg"
              style={{ backgroundColor: "#071b19", border: "1px solid #04594D" }}
            >
              <div className="flex items-center justify-between">
                <span style={{ color: "#9ca3af", fontSize: "11px", fontWeight: 500 }}>{stat.label}</span>
                <Icon size={14} style={{ color: stat.color }} />
              </div>
              <span style={{ color: "#e9fff7", fontSize: "22px", fontWeight: 700 }}>{stat.value}</span>
            </div>
          );
        })}
      </div>

      {/* Блок 2 — График активности по дням */}
      <div
        className="rounded-lg overflow-hidden"
        style={{ backgroundColor: "#071b19", border: "1px solid #04594D" }}
      >
        <div className="px-5 py-4" style={{ borderBottom: "1px solid #04594D" }}>
          <h2 style={{ color: "#e9fff7", fontSize: "16px", fontWeight: 600 }}>
            Активность за последние 90 дней
          </h2>
          <p style={{ color: "#9ca3af", fontSize: "12px", marginTop: "4px" }}>
            Количество задач и объём загруженных данных
          </p>
        </div>
        <div className="p-5">
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={activityData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#04594D" />
              <XAxis
                dataKey="date"
                stroke="#9ca3af"
                style={{ fontSize: "10px" }}
                interval={9}
              />
              <YAxis
                yAxisId="left"
                stroke="#1faa89"
                style={{ fontSize: "11px" }}
                label={{ value: "Задачи", angle: -90, position: "insideLeft", fill: "#9ca3af", fontSize: 11 }}
              />
              <YAxis
                yAxisId="right"
                orientation="right"
                stroke="#f59e0b"
                style={{ fontSize: "11px" }}
                label={{ value: "МБ", angle: 90, position: "insideRight", fill: "#9ca3af", fontSize: 11 }}
              />
              <Tooltip content={<CustomTooltip />} />
              <Legend wrapperStyle={{ fontSize: "12px", paddingTop: "10px" }} iconType="line" />
              <Line yAxisId="left" type="monotone" dataKey="tasks" stroke="#1faa89" strokeWidth={2} dot={false} name="Задачи" />
              <Line yAxisId="right" type="monotone" dataKey="mb" stroke="#f59e0b" strokeWidth={2} strokeDasharray="5 5" dot={false} name="МБ" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Блоки 3 и 4 — две колонки */}
      <div className="grid grid-cols-2 gap-4">
        {/* Блок 3 — Распределение по источникам */}
        <div
          className="rounded-lg overflow-hidden"
          style={{ backgroundColor: "#071b19", border: "1px solid #04594D" }}
        >
          <div className="px-5 py-4" style={{ borderBottom: "1px solid #04594D" }}>
            <h2 style={{ color: "#e9fff7", fontSize: "16px", fontWeight: 600 }}>
              Распределение по источникам
            </h2>
          </div>
          <div className="p-5">
            <ResponsiveContainer width="100%" height={240}>
              <PieChart>
                <Pie
                  data={sources}
                  dataKey="count"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  outerRadius={90}
                  label={(entry) => entry.name}
                  labelLine={true}
                >
                  {sources.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip content={<CustomTooltip />} />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Блок 4 — Топ сайтов */}
        <div
          className="rounded-lg overflow-hidden"
          style={{ backgroundColor: "#071b19", border: "1px solid #04594D" }}
        >
          <div className="px-5 py-4" style={{ borderBottom: "1px solid #04594D" }}>
            <h2 style={{ color: "#e9fff7", fontSize: "16px", fontWeight: 600 }}>Топ сайтов</h2>
          </div>
          <div className="p-5">
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={topSites} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="#04594D" />
                <XAxis type="number" stroke="#9ca3af" style={{ fontSize: "11px" }} />
                <YAxis
                  type="category"
                  dataKey="domain"
                  stroke="#9ca3af"
                  style={{ fontSize: "11px" }}
                  width={120}
                />
                <Tooltip content={<CustomTooltip />} />
                <Bar dataKey="count" fill="#22c55e" radius={[0, 4, 4, 0]} animationDuration={800} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Блоки 5 и 6 — две колонки */}
      <div className="grid grid-cols-2 gap-4">
        {/* Блок 5 — Активность пользователей */}
        <div
          className="rounded-lg overflow-hidden"
          style={{ backgroundColor: "#071b19", border: "1px solid #04594D" }}
        >
          <div className="px-5 py-4" style={{ borderBottom: "1px solid #04594D" }}>
            <h2 style={{ color: "#e9fff7", fontSize: "16px", fontWeight: 600 }}>Активность пользователей</h2>
          </div>
          <div className="overflow-x-auto">
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ backgroundColor: "#0a1f1a" }}>
                  <th style={{ padding: "12px 20px", textAlign: "left", color: "#9ca3af", fontSize: "11px", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px" }}>
                    ID пользователя
                  </th>
                  <th style={{ padding: "12px 20px", textAlign: "right", color: "#9ca3af", fontSize: "11px", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px" }}>
                    Загрузки
                  </th>
                </tr>
              </thead>
              <tbody>
                {userActivity.length === 0 ? (
                  <tr>
                    <td colSpan={2} style={{ padding: "20px", textAlign: "center", color: "#6b7280", fontSize: "13px" }}>
                      Нет данных
                    </td>
                  </tr>
                ) : (
                  userActivity.map((user, index) => (
                    <tr
                      key={user.userId}
                      style={{
                        backgroundColor: index % 2 === 0 ? "#071b19" : "#0a1f1a",
                        borderBottom: index < userActivity.length - 1 ? "1px solid #04594D" : "none",
                      }}
                    >
                      <td style={{ padding: "14px 20px", color: "#e9fff7", fontSize: "13px", fontFamily: "monospace" }}>
                        {user.userId}
                      </td>
                      <td style={{ padding: "14px 20px", textAlign: "right", color: "#1faa89", fontSize: "14px", fontWeight: 600 }}>
                        {user.downloads.toLocaleString()}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Блок 6 — Системный статус */}
        <div
          className="rounded-lg overflow-hidden"
          style={{ backgroundColor: "#071b19", border: "1px solid #04594D" }}
        >
          <div className="px-5 py-4" style={{ borderBottom: "1px solid #04594D" }}>
            <h2 style={{ color: "#e9fff7", fontSize: "16px", fontWeight: 600 }}>Системный статус</h2>
          </div>
          <div className="p-5 flex flex-col gap-3">
            {systemStatus.map((system) => {
              const Icon = system.icon;
              return (
                <div
                  key={system.name}
                  className="flex items-center gap-4 p-4 rounded-lg"
                  style={{ backgroundColor: "#0a1f1a", border: "1px solid #04594D" }}
                >
                  <div
                    className="flex items-center justify-center"
                    style={{ width: "40px", height: "40px", borderRadius: "8px", backgroundColor: system.color + "20" }}
                  >
                    <Icon size={20} style={{ color: system.color }} />
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span style={{ color: "#e9fff7", fontSize: "14px", fontWeight: 600 }}>{system.name}</span>
                      <span
                        className="px-2 py-0.5 rounded text-xs"
                        style={{ backgroundColor: system.color + "20", color: system.color, fontSize: "10px", fontWeight: 600, textTransform: "uppercase" }}
                      >
                        {system.status === "online" ? "онлайн" : system.status === "active" ? "активен" : system.status === "offline" ? "офлайн" : system.status}
                      </span>
                    </div>
                    <p style={{ color: "#9ca3af", fontSize: "12px", marginTop: "4px" }}>{system.info}</p>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
