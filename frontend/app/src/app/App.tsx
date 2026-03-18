import { BrowserRouter, Routes, Route, Navigate, Outlet } from "react-router";
import { Sidebar } from "./components/Sidebar";
import Login from "./pages/Login";
import Registration from "./pages/Registration";
import { DownloadsPage } from "./components/DownloadsPage";
import { ActiveDownloadsPage } from "./components/ActiveDownloadsPage";
import { HistoryPage } from "./components/HistoryPage";
import { DashboardPage } from "./components/DashboardPage";
import { PreferencesPage } from "./components/PreferencesPage";
import { LogPage } from "./components/LogConsole";

// ─── Вспомогательные страницы ────────────────────────────────────────────────

function ReadmePage() {
  return (
    <div className="flex flex-col gap-5">
      <h1 style={{ fontSize: "28px", fontWeight: 700, color: "#FFFFFF" }}>README</h1>
      <div
        className="p-5 rounded-xl flex flex-col gap-4"
        style={{ backgroundColor: "#152019", border: "1px solid #04594D" }}
      >
        <h2 style={{ color: "#22C55E", fontSize: "18px", fontWeight: 700 }}>GrabVidZilla v2</h2>
        <p style={{ color: "#9CA3AF", fontSize: "13px", lineHeight: 1.7 }}>
          GrabVidZilla — мощный инструмент для скачивания видео с YouTube, Vimeo, Instagram,
          TikTok, Twitter/X, Facebook и сотен других платформ.
        </p>
        {[
          {
            title: "Как использовать",
            content:
              "1. Вставьте URL видео в поле ввода.\n2. Нажмите «Анализ» — будут показаны название и доступные качества.\n3. Выберите качество и нажмите «Скачать».",
          },
          { title: "Поддерживаемые форматы", content: "MP4, MKV, AVI, WebM, MOV, MP3, FLAC, WAV" },
          {
            title: "Горячие клавиши",
            content: "Ctrl+V — вставить URL\nEnter — начать анализ",
          },
        ].map((section) => (
          <div key={section.title} className="flex flex-col gap-2">
            <span style={{ color: "#D1D5DB", fontSize: "13px", fontWeight: 600 }}>
              {section.title}
            </span>
            <pre
              style={{ color: "#9CA3AF", fontSize: "12px", lineHeight: 1.7, whiteSpace: "pre-wrap" }}
            >
              {section.content}
            </pre>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Защита роутов ────────────────────────────────────────────────────────────

/** Редирект на /login если нет активной сессии в localStorage. */
function PrivateRoute() {
  const user = localStorage.getItem("user");
  return user ? <AppLayout /> : <Navigate to="/login" replace />;
}

/** Layout: Sidebar + область контента с <Outlet /> для дочерних маршрутов. */
function AppLayout() {
  return (
    <div
      className="flex h-screen w-screen overflow-hidden"
      style={{ backgroundColor: "#000D0B" }}
    >
      <Sidebar />
      <div className="flex-1 flex flex-col p-6 overflow-hidden">
        <div
          className="flex-1 rounded-xl overflow-y-auto"
          style={{
            backgroundColor: "#000D0B",
            border: "1px solid #04594D",
            padding: "32px",
          }}
        >
          <Outlet />
        </div>
      </div>
    </div>
  );
}

// ─── Корневой компонент ───────────────────────────────────────────────────────

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Публичные страницы */}
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Registration />} />

        {/* Приватные страницы внутри AppLayout */}
        <Route element={<PrivateRoute />}>
          <Route path="/" element={<DownloadsPage />} />
          <Route path="/active" element={<ActiveDownloadsPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/log" element={<LogPage />} />
          <Route path="/readme" element={<ReadmePage />} />
          <Route path="/preferences" element={<PreferencesPage />} />
        </Route>

        {/* Всё остальное — на главную */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
