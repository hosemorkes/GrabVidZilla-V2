import { useNavigate, useLocation } from "react-router";
import { Download, List, Clock, LayoutDashboard, FileText, BookOpen, Settings, LogOut } from "lucide-react";
import logoImage from "@/assets/grabvidzilla-logo.png";

// Маппинг id пункта меню → URL маршрут
const ROUTES: Record<string, string> = {
  downloads: "/",
  active: "/active",
  history: "/history",
  dashboard: "/dashboard",
  log: "/log",
  readme: "/readme",
  preferences: "/preferences",
};

const topMenuItems = [
  { id: "downloads", label: "Downloads", icon: Download },
  { id: "active", label: "Active downloads", icon: List },
  { id: "history", label: "History", icon: Clock },
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
];

const bottomMenuItems = [
  { id: "log", label: "Log", icon: FileText },
  { id: "readme", label: "Readme", icon: BookOpen },
  { id: "preferences", label: "Preferenc", icon: Settings },
  { id: "exit", label: "Exit app", icon: LogOut },
];

export function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();

  // Пользователь из сессии localStorage
  const userRaw = localStorage.getItem("user");
  const user = userRaw ? JSON.parse(userRaw) : null;

  // Определяем активный пункт по текущему pathname
  const getActiveTab = (): string => {
    for (const [id, path] of Object.entries(ROUTES)) {
      if (path === "/" ? location.pathname === "/" : location.pathname.startsWith(path)) {
        return id;
      }
    }
    return "downloads";
  };

  const activeTab = getActiveTab();

  const handleTabChange = (id: string) => {
    if (id === "exit") {
      if (window.confirm("Are you sure you want to exit GrabVidZilla?")) {
        localStorage.removeItem("user");
        navigate("/login");
      }
      return;
    }
    const route = ROUTES[id];
    if (route) navigate(route);
  };

  return (
    <div
      className="flex flex-col h-full py-4"
      style={{
        width: "200px",
        minWidth: "200px",
        backgroundColor: "#001212",
        borderRight: "1px solid #04594D",
      }}
    >
      {/* Logo */}
      <div className="flex flex-col items-center gap-1 px-[16px] pt-[8px] pb-[24px]">
        <img src={logoImage} alt="Logo" className="w-full h-auto rounded-[16px]" />
      </div>

      {/* Top Navigation */}
      <nav className="flex flex-col gap-1 px-2 flex-1">
        {topMenuItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => handleTabChange(item.id)}
              className="flex items-center gap-2 px-2 rounded-md transition-all cursor-pointer w-full text-left"
              style={{
                height: "36px",
                backgroundColor: isActive ? "#1E3328" : "transparent",
                color: isActive ? "#22C55E" : "#FFFFFF",
                fontWeight: isActive ? 600 : 400,
                fontSize: "16px",
              }}
            >
              <Icon size={15} style={{ color: isActive ? "#22C55E" : "#FFFFFF", flexShrink: 0 }} />
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>

      {/* Имя текущего пользователя */}
      {user && (
        <div className="px-4 py-2" style={{ borderTop: "1px solid #04594D" }}>
          <p style={{ color: "#9CA3AF", fontSize: "11px" }}>Вы вошли как</p>
          <p
            className="truncate"
            style={{ color: "#22C55E", fontSize: "12px", fontWeight: 600 }}
          >
            {user.name || user.email || user.user_id}
          </p>
        </div>
      )}

      {/* Bottom Navigation */}
      <nav className="flex flex-col gap-1 px-2">
        {bottomMenuItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.id;
          const isExit = item.id === "exit";
          return (
            <button
              key={item.id}
              onClick={() => handleTabChange(item.id)}
              className="flex items-center gap-2 px-2 rounded-md transition-all cursor-pointer w-full text-left"
              style={{
                height: "36px",
                backgroundColor: isActive ? "#1E3328" : "transparent",
                color: isExit ? "#EF4444" : isActive ? "#22C55E" : "#FFFFFF",
                fontWeight: isActive ? 600 : 400,
                fontSize: "16px",
              }}
            >
              <Icon
                size={15}
                style={{
                  color: isExit ? "#EF4444" : isActive ? "#22C55E" : "#FFFFFF",
                  flexShrink: 0,
                }}
              />
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>
    </div>
  );
}
