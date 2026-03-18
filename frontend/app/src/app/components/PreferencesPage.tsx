import { useState, useEffect } from "react";
import type { CSSProperties } from "react";
import { Save, Plus, Edit2, Trash2 } from "lucide-react";
import api from "@/api/client";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "./ui/tabs";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose,
} from "./ui/dialog";
import {
  AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle,
  AlertDialogDescription, AlertDialogFooter, AlertDialogCancel, AlertDialogAction,
} from "./ui/alert-dialog";

// ─── Типы ─────────────────────────────────────────────────────────────────────

interface StoredUser {
  user_id: number;
  name: string;
  role: string;
}

interface UserData {
  id: number;
  name: string;
  email: string;
  role: string;
  is_active: boolean;
  telegram_chat_id: string | null;
  created_at: string | null;
}

// ─── Вспомогательные компоненты ───────────────────────────────────────────────

function RoleBadge({ role }: { role: string }) {
  const map: Record<string, [string, string]> = {
    root:  ["#1faa8920", "#1faa89"],
    admin: ["#f59e0b20", "#f59e0b"],
    user:  ["#6b728020", "#9ca3af"],
  };
  const [bg, color] = map[role] ?? map.user;
  return (
    <span style={{
      background: bg, color,
      padding: "2px 10px", borderRadius: "4px",
      fontSize: "11px", fontWeight: 700,
      textTransform: "uppercase", letterSpacing: "0.5px",
    }}>
      {role}
    </span>
  );
}

interface FieldProps {
  label: string;
  error?: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}

function Field({ label, error, hint, children }: FieldProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
      <label style={{
        color: "#9ca3af", fontSize: "12px", fontWeight: 600,
        textTransform: "uppercase", letterSpacing: "0.5px",
      }}>
        {label}
      </label>
      {children}
      {hint && <span style={{ color: "#9ca3af", fontSize: "12px" }}>{hint}</span>}
      {error && <span style={{ color: "#ef4444", fontSize: "12px" }}>{error}</span>}
    </div>
  );
}

// ─── Общие стили ──────────────────────────────────────────────────────────────

const inputStyle: CSSProperties = {
  backgroundColor: "#0a1f1a",
  border: "1px solid #04594D",
  color: "#e9fff7",
  fontSize: "14px",
  borderRadius: "8px",
  padding: "10px 14px",
  outline: "none",
  width: "100%",
  boxSizing: "border-box",
};

const btnPrimary: CSSProperties = {
  backgroundColor: "#1faa89",
  color: "#e9fff7",
  border: "1px solid #1faa89",
  borderRadius: "8px",
  padding: "9px 18px",
  fontSize: "14px",
  fontWeight: 600,
  cursor: "pointer",
  display: "inline-flex",
  alignItems: "center",
  gap: "6px",
};

const btnSecondary: CSSProperties = {
  backgroundColor: "#0a1f1a",
  color: "#9ca3af",
  border: "1px solid #04594D",
  borderRadius: "8px",
  padding: "9px 18px",
  fontSize: "14px",
  cursor: "pointer",
};

const btnDanger: CSSProperties = {
  backgroundColor: "#ef444415",
  color: "#ef4444",
  border: "1px solid #ef444440",
  borderRadius: "6px",
  padding: "6px 10px",
  cursor: "pointer",
  display: "inline-flex",
  alignItems: "center",
};

const cardStyle: CSSProperties = {
  backgroundColor: "#071b19",
  border: "1px solid #04594D",
  borderRadius: "12px",
  overflow: "hidden",
};

const cardHeaderStyle: CSSProperties = {
  padding: "16px 20px",
  borderBottom: "1px solid #04594D",
  backgroundColor: "#0a1f1a",
};

// ─── Вспомогательные функции ──────────────────────────────────────────────────

function formatDate(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("ru-RU", {
    day: "2-digit", month: "2-digit", year: "numeric",
  });
}

function onlyDigits(value: string) {
  return value.replace(/\D/g, "");
}

// ─── Компонент ────────────────────────────────────────────────────────────────

export function PreferencesPage() {
  // Текущий пользователь из localStorage
  const stored: Partial<StoredUser> = JSON.parse(localStorage.getItem("user") || "{}");
  const currentUser: StoredUser = {
    user_id: stored.user_id ?? 0,
    name: stored.name ?? "",
    role: stored.role ?? "user",
  };
  const isRoot = currentUser.role === "root";

  const [activeTab, setActiveTab] = useState("account");

  // Уведомление с автоскрытием
  const [notice, setNotice] = useState<{ type: "success" | "error"; msg: string } | null>(null);
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 3500);
    return () => clearTimeout(t);
  }, [notice]);

  // ── Вкладка «Мой аккаунт» ──────────────────────────────────────────────────

  const [accountForm, setAccountForm] = useState({
    name: currentUser.name,
    email: "",
    password: "",
    confirmPassword: "",
    telegramId: "",
  });
  const [pwdError, setPwdError]     = useState("");
  const [emailError, setEmailError] = useState("");
  const [tgError, setTgError]       = useState("");
  const [isSaving, setIsSaving]   = useState(false);

  // Загружаем email и telegram_chat_id из API для всех ролей
  useEffect(() => {
    if (!currentUser.user_id) return;
    api.get("/users/me", {
      params: { caller_user_id: currentUser.user_id },
    })
      .then(({ data }) => {
        const me = data as UserData;
        setAccountForm(f => ({
          ...f,
          email: me.email,
          telegramId: me.telegram_chat_id ?? "",
        }));
      })
      .catch(() => { /* игнорируем — поля останутся пустыми */ });
  }, []);

  const handleSaveMe = async () => {
    if (accountForm.password && accountForm.password !== accountForm.confirmPassword) {
      setPwdError("Пароли не совпадают");
      return;
    }
    setPwdError("");
    setEmailError("");
    setTgError("");
    setIsSaving(true);
    try {
      const payload: Record<string, unknown> = {};
      if (accountForm.name.trim())       payload.name             = accountForm.name.trim();
      if (accountForm.email.trim())      payload.email            = accountForm.email.trim();
      if (accountForm.password)          payload.password         = accountForm.password;
      if (accountForm.telegramId.trim()) payload.telegram_chat_id = accountForm.telegramId.trim();

      await api.put("/users/me", payload, {
        params: { caller_user_id: currentUser.user_id },
      });

      // Обновляем имя в localStorage если оно изменилось
      if (payload.name) {
        localStorage.setItem("user", JSON.stringify({ ...stored, name: payload.name }));
      }
      setAccountForm(f => ({ ...f, password: "", confirmPassword: "" }));
      setNotice({ type: "success", msg: "Данные сохранены" });
    } catch (err: unknown) {
      const e = err as { response?: { status?: number; data?: { detail?: string } } };
      const status = e.response?.status;
      const detail = e.response?.data?.detail ?? "";
      if (status === 409 && detail.toLowerCase().includes("telegram")) {
        setTgError(detail);
      } else if (status === 409 && detail.toLowerCase().includes("email")) {
        setEmailError(detail);
      } else {
        setNotice({ type: "error", msg: detail || "Ошибка сохранения" });
      }
    } finally {
      setIsSaving(false);
    }
  };

  // ── Вкладка «Пользователи» (только root) ───────────────────────────────────

  const [users, setUsers]             = useState<UserData[]>([]);
  const [loadingUsers, setLoadingUsers] = useState(false);
  const rootParams = { caller_user_id: currentUser.user_id, caller_role: currentUser.role };

  const fetchUsers = async () => {
    setLoadingUsers(true);
    try {
      const { data } = await api.get("/users", { params: rootParams });
      setUsers(data as UserData[]);
    } catch {
      setNotice({ type: "error", msg: "Не удалось загрузить пользователей" });
    } finally {
      setLoadingUsers(false);
    }
  };

  useEffect(() => {
    if (activeTab === "users" && isRoot) fetchUsers();
  }, [activeTab]);

  // ── Диалог создания пользователя ───────────────────────────────────────────

  const emptyCreate = { name: "", email: "", password: "", role: "user", telegramId: "" };
  const [createOpen, setCreateOpen]       = useState(false);
  const [createForm, setCreateForm]       = useState(emptyCreate);
  const [createErrors, setCreateErrors]   = useState<Record<string, string>>({});
  const [isCreating, setIsCreating]       = useState(false);

  const handleCreate = async () => {
    const errs: Record<string, string> = {};
    if (!createForm.name.trim())    errs.name     = "Обязательное поле";
    if (!createForm.email.trim())   errs.email    = "Обязательное поле";
    if (!createForm.password)       errs.password = "Обязательное поле";
    if (Object.keys(errs).length)   { setCreateErrors(errs); return; }
    setCreateErrors({});
    setIsCreating(true);
    try {
      const payload: Record<string, unknown> = {
        name: createForm.name.trim(),
        email: createForm.email.trim(),
        password: createForm.password,
        role: createForm.role,
      };
      if (createForm.telegramId.trim()) payload.telegram_chat_id = createForm.telegramId.trim();
      await api.post("/users", payload, { params: rootParams });
      setCreateOpen(false);
      setCreateForm(emptyCreate);
      await fetchUsers();
      setNotice({ type: "success", msg: "Пользователь создан" });
    } catch (err: unknown) {
      const e = err as { response?: { status?: number; data?: { detail?: string } } };
      const detail = e.response?.data?.detail ?? "";
      if (e.response?.status === 409 && detail.toLowerCase().includes("email")) {
        setCreateErrors({ email: detail });
      } else {
        setNotice({ type: "error", msg: detail || "Ошибка создания" });
        setCreateOpen(false);
      }
    } finally {
      setIsCreating(false);
    }
  };

  // ── Диалог редактирования пользователя ─────────────────────────────────────

  const [editOpen, setEditOpen]       = useState(false);
  const [editTarget, setEditTarget]   = useState<UserData | null>(null);
  const [editForm, setEditForm]       = useState({ name: "", email: "", password: "", role: "user", is_active: true, telegramId: "" });
  const [editError, setEditError]     = useState("");
  const [isEditing, setIsEditing]     = useState(false);

  const openEdit = (user: UserData) => {
    setEditTarget(user);
    setEditForm({ name: user.name, email: user.email, password: "", role: user.role, is_active: user.is_active, telegramId: user.telegram_chat_id ?? "" });
    setEditError("");
    setEditOpen(true);
  };

  const handleEdit = async () => {
    if (!editTarget) return;
    setEditError("");
    setIsEditing(true);
    try {
      const payload: Record<string, unknown> = {
        name: editForm.name.trim() || undefined,
        role: editForm.role,
        is_active: editForm.is_active,
      };
      if (editForm.email.trim())        payload.email            = editForm.email.trim();
      if (editForm.password)            payload.password         = editForm.password;
      if (editForm.telegramId.trim())   payload.telegram_chat_id = editForm.telegramId.trim();
      await api.put(`/users/${editTarget.id}`, payload, { params: rootParams });
      setEditOpen(false);
      await fetchUsers();
      setNotice({ type: "success", msg: "Пользователь обновлён" });
    } catch (err: unknown) {
      const e = err as { response?: { status?: number; data?: { detail?: string } } };
      setEditError(e.response?.data?.detail ?? "Ошибка сохранения");
    } finally {
      setIsEditing(false);
    }
  };

  // ── Диалог удаления пользователя ───────────────────────────────────────────

  const [deleteOpen, setDeleteOpen]   = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<UserData | null>(null);
  const [isDeleting, setIsDeleting]   = useState(false);

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      await api.delete(`/users/${deleteTarget.id}`, { params: rootParams });
      setDeleteOpen(false);
      await fetchUsers();
      setNotice({ type: "success", msg: "Пользователь удалён" });
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } };
      setNotice({ type: "error", msg: e.response?.data?.detail ?? "Ошибка удаления" });
      setDeleteOpen(false);
    } finally {
      setIsDeleting(false);
    }
  };

  // ── Рендер ─────────────────────────────────────────────────────────────────

  const tabTriggerClass =
    "rounded-none h-auto py-3 px-5 text-sm font-semibold " +
    "border-b-2 border-transparent bg-transparent " +
    "data-[state=active]:bg-transparent data-[state=active]:shadow-none " +
    "data-[state=active]:text-[#1faa89] data-[state=active]:border-[#1faa89] " +
    "text-[#9ca3af] hover:text-[#e9fff7] transition-colors";

  return (
    <div className="flex flex-col gap-6 pb-8">
      <h1 style={{ fontSize: "28px", fontWeight: 700, color: "#e9fff7" }}>Настройки</h1>

      {/* Уведомление */}
      {notice && (
        <div style={{
          padding: "12px 16px", borderRadius: "8px", fontSize: "14px", fontWeight: 500,
          backgroundColor: notice.type === "success" ? "#1faa8920" : "#ef444420",
          border:          `1px solid ${notice.type === "success" ? "#1faa89" : "#ef4444"}`,
          color:           notice.type === "success" ? "#1faa89" : "#ef4444",
        }}>
          {notice.type === "success" ? "✓ " : "✕ "}{notice.msg}
        </div>
      )}

      {/* Вкладки */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList
          className="bg-transparent rounded-none h-auto p-0 w-full justify-start gap-0"
          style={{ borderBottom: "1px solid #04594D" }}
        >
          <TabsTrigger value="account" className={tabTriggerClass}>
            Мой аккаунт
          </TabsTrigger>
          {isRoot && (
            <TabsTrigger value="users" className={tabTriggerClass}>
              Пользователи
            </TabsTrigger>
          )}
        </TabsList>

        {/* ── Вкладка 1: Мой аккаунт ────────────────────────────────────────── */}
        <TabsContent value="account" className="mt-6">
          <div style={cardStyle}>
            <div style={cardHeaderStyle}>
              <h2 style={{ color: "#e9fff7", fontSize: "16px", fontWeight: 600 }}>Данные аккаунта</h2>
            </div>
            <div style={{ padding: "24px", display: "flex", flexDirection: "column", gap: "20px" }}>
              {/* Сетка 2 колонки */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "20px" }}>
                <Field label="Имя">
                  <input
                    type="text"
                    value={accountForm.name}
                    onChange={e => setAccountForm(f => ({ ...f, name: e.target.value }))}
                    placeholder="Ваше имя"
                    style={inputStyle}
                  />
                </Field>
                <Field label="Email" error={emailError}>
                  <input
                    type="email"
                    value={accountForm.email}
                    onChange={e => { setAccountForm(f => ({ ...f, email: e.target.value })); setEmailError(""); }}
                    placeholder="user@example.com"
                    style={inputStyle}
                  />
                </Field>
                <Field label="Новый пароль" error={pwdError}>
                  <input
                    type="password"
                    value={accountForm.password}
                    onChange={e => { setAccountForm(f => ({ ...f, password: e.target.value })); setPwdError(""); }}
                    placeholder="Оставьте пустым, чтобы не менять"
                    style={inputStyle}
                  />
                </Field>
                <Field label="Подтверждение пароля">
                  <input
                    type="password"
                    value={accountForm.confirmPassword}
                    onChange={e => { setAccountForm(f => ({ ...f, confirmPassword: e.target.value })); setPwdError(""); }}
                    placeholder="••••••••"
                    style={inputStyle}
                  />
                </Field>
              </div>

              {/* Telegram ID — полная ширина */}
              <Field
                label="Telegram ID"
                error={tgError}
                hint={
                  <>
                    Узнать свой ID можно у{" "}
                    <a
                      href="https://t.me/userinfobot"
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ color: "#1faa89", textDecoration: "underline" }}
                    >
                      @userinfobot
                    </a>{" "}
                    в Telegram
                  </>
                }
              >
                <input
                  type="text"
                  value={accountForm.telegramId}
                  onChange={e => { setAccountForm(f => ({ ...f, telegramId: onlyDigits(e.target.value) })); setTgError(""); }}
                  placeholder="Только цифры, например 123456789"
                  style={{ ...inputStyle, maxWidth: "420px" }}
                />
              </Field>

              <div style={{ display: "flex", justifyContent: "flex-end" }}>
                <button
                  onClick={handleSaveMe}
                  disabled={isSaving}
                  style={{ ...btnPrimary, opacity: isSaving ? 0.7 : 1 }}
                >
                  <Save size={15} />
                  {isSaving ? "Сохранение..." : "Сохранить"}
                </button>
              </div>
            </div>
          </div>
        </TabsContent>

        {/* ── Вкладка 2: Пользователи (только root) ────────────────────────── */}
        {isRoot && (
          <TabsContent value="users" className="mt-6">
            <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>

              {/* Кнопка добавления */}
              <div style={{ display: "flex", justifyContent: "flex-end" }}>
                <button
                  onClick={() => { setCreateForm(emptyCreate); setCreateErrors({}); setCreateOpen(true); }}
                  style={btnPrimary}
                >
                  <Plus size={15} />
                  Добавить пользователя
                </button>
              </div>

              {/* Таблица пользователей */}
              <div style={cardStyle}>
                <div style={cardHeaderStyle}>
                  <h2 style={{ color: "#e9fff7", fontSize: "16px", fontWeight: 600 }}>
                    Пользователи{users.length > 0 ? ` (${users.length})` : ""}
                  </h2>
                </div>
                {loadingUsers ? (
                  <div style={{ padding: "40px", textAlign: "center", color: "#9ca3af" }}>Загрузка...</div>
                ) : (
                  <div style={{ overflowX: "auto" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse" }}>
                      <thead>
                        <tr style={{ backgroundColor: "#0a1f1a" }}>
                          {(["Имя", "Email", "Роль", "Статус", "Telegram ID", "Дата", "Действия"] as const).map(col => (
                            <th key={col} style={{
                              padding: "12px 16px", textAlign: "left",
                              color: "#9ca3af", fontSize: "11px", fontWeight: 600,
                              textTransform: "uppercase", letterSpacing: "0.5px", whiteSpace: "nowrap",
                            }}>
                              {col}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {users.length === 0 && (
                          <tr>
                            <td colSpan={7} style={{ padding: "40px", textAlign: "center", color: "#6b7280" }}>
                              Нет пользователей
                            </td>
                          </tr>
                        )}
                        {users.map((user, idx) => {
                          const isRootUser = user.role === "root";
                          return (
                            <tr key={user.id} style={{
                              backgroundColor: idx % 2 === 0 ? "#071b19" : "#0a1f1a",
                              borderTop: "1px solid #04594D30",
                            }}>
                              <td style={{ padding: "14px 16px", color: "#e9fff7", fontSize: "13px", fontWeight: 500 }}>{user.name}</td>
                              <td style={{ padding: "14px 16px", color: "#9ca3af", fontSize: "13px" }}>{user.email}</td>
                              <td style={{ padding: "14px 16px" }}><RoleBadge role={user.role} /></td>
                              <td style={{ padding: "14px 16px" }}>
                                <span style={{ color: user.is_active ? "#1faa89" : "#6b7280", fontSize: "13px" }}>
                                  {user.is_active ? "Активен" : "Деактивирован"}
                                </span>
                              </td>
                              <td style={{ padding: "14px 16px", color: "#9ca3af", fontSize: "13px" }}>
                                {user.telegram_chat_id || "—"}
                              </td>
                              <td style={{ padding: "14px 16px", color: "#9ca3af", fontSize: "12px", whiteSpace: "nowrap" }}>
                                {formatDate(user.created_at)}
                              </td>
                              <td style={{ padding: "14px 16px" }}>
                                <div style={{ display: "flex", gap: "6px" }}>
                                  <button
                                    onClick={() => openEdit(user)}
                                    disabled={isRootUser}
                                    title={isRootUser ? "Нельзя изменить root" : "Редактировать"}
                                    style={{
                                      backgroundColor: "#0a1f1a",
                                      border: "1px solid #04594D",
                                      color: "#9ca3af",
                                      borderRadius: "6px",
                                      padding: "6px 10px",
                                      cursor: isRootUser ? "not-allowed" : "pointer",
                                      opacity: isRootUser ? 0.35 : 1,
                                      display: "inline-flex",
                                      alignItems: "center",
                                    }}
                                  >
                                    <Edit2 size={14} />
                                  </button>
                                  <button
                                    onClick={() => { setDeleteTarget(user); setDeleteOpen(true); }}
                                    disabled={isRootUser}
                                    title={isRootUser ? "Нельзя удалить root" : "Удалить"}
                                    style={{ ...btnDanger, cursor: isRootUser ? "not-allowed" : "pointer", opacity: isRootUser ? 0.35 : 1 }}
                                  >
                                    <Trash2 size={14} />
                                  </button>
                                </div>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>

            {/* ── Диалог создания ──────────────────────────────────────────── */}
            <Dialog open={createOpen} onOpenChange={setCreateOpen}>
              <DialogContent style={{ backgroundColor: "#071b19", border: "1px solid #04594D", color: "#e9fff7" }}>
                <DialogHeader>
                  <DialogTitle style={{ color: "#e9fff7" }}>Создать пользователя</DialogTitle>
                </DialogHeader>
                <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
                  <Field label="Имя *" error={createErrors.name}>
                    <input
                      type="text"
                      value={createForm.name}
                      onChange={e => setCreateForm(f => ({ ...f, name: e.target.value }))}
                      placeholder="Иван Иванов"
                      style={inputStyle}
                    />
                  </Field>
                  <Field label="Email *" error={createErrors.email}>
                    <input
                      type="email"
                      value={createForm.email}
                      onChange={e => { setCreateForm(f => ({ ...f, email: e.target.value })); setCreateErrors(er => ({ ...er, email: "" })); }}
                      placeholder="user@example.com"
                      style={inputStyle}
                    />
                  </Field>
                  <Field label="Пароль *" error={createErrors.password}>
                    <input
                      type="password"
                      value={createForm.password}
                      onChange={e => setCreateForm(f => ({ ...f, password: e.target.value }))}
                      placeholder="••••••••"
                      style={inputStyle}
                    />
                  </Field>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "14px" }}>
                    <Field label="Роль">
                      <select
                        value={createForm.role}
                        onChange={e => setCreateForm(f => ({ ...f, role: e.target.value }))}
                        style={{ ...inputStyle, cursor: "pointer" }}
                      >
                        <option value="user">user</option>
                        <option value="admin">admin</option>
                      </select>
                    </Field>
                    <Field label="Telegram ID">
                      <input
                        type="text"
                        value={createForm.telegramId}
                        onChange={e => setCreateForm(f => ({ ...f, telegramId: onlyDigits(e.target.value) }))}
                        placeholder="Только цифры"
                        style={inputStyle}
                      />
                    </Field>
                  </div>
                </div>
                <DialogFooter>
                  <DialogClose asChild>
                    <button style={btnSecondary}>Отмена</button>
                  </DialogClose>
                  <button onClick={handleCreate} disabled={isCreating} style={{ ...btnPrimary, opacity: isCreating ? 0.7 : 1 }}>
                    <Plus size={14} />
                    {isCreating ? "Создание..." : "Создать"}
                  </button>
                </DialogFooter>
              </DialogContent>
            </Dialog>

            {/* ── Диалог редактирования ────────────────────────────────────── */}
            <Dialog open={editOpen} onOpenChange={setEditOpen}>
              <DialogContent style={{ backgroundColor: "#071b19", border: "1px solid #04594D", color: "#e9fff7" }}>
                <DialogHeader>
                  <DialogTitle style={{ color: "#e9fff7" }}>
                    Редактировать: {editTarget?.name}
                  </DialogTitle>
                </DialogHeader>
                <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "14px" }}>
                    <Field label="Имя">
                      <input
                        type="text"
                        value={editForm.name}
                        onChange={e => setEditForm(f => ({ ...f, name: e.target.value }))}
                        style={inputStyle}
                      />
                    </Field>
                    <Field label="Email">
                      <input
                        type="email"
                        value={editForm.email}
                        onChange={e => setEditForm(f => ({ ...f, email: e.target.value }))}
                        placeholder="user@example.com"
                        style={inputStyle}
                      />
                    </Field>
                  </div>
                  <Field label="Новый пароль">
                    <input
                      type="password"
                      value={editForm.password}
                      onChange={e => setEditForm(f => ({ ...f, password: e.target.value }))}
                      placeholder="Оставьте пустым, чтобы не менять"
                      style={inputStyle}
                    />
                  </Field>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "14px" }}>
                    <Field label="Роль">
                      <select
                        value={editForm.role}
                        onChange={e => setEditForm(f => ({ ...f, role: e.target.value }))}
                        style={{ ...inputStyle, cursor: "pointer" }}
                      >
                        <option value="user">user</option>
                        <option value="admin">admin</option>
                      </select>
                    </Field>
                    <Field label="Telegram ID">
                      <input
                        type="text"
                        value={editForm.telegramId}
                        onChange={e => setEditForm(f => ({ ...f, telegramId: onlyDigits(e.target.value) }))}
                        placeholder="Только цифры"
                        style={inputStyle}
                      />
                    </Field>
                  </div>
                  <label style={{ display: "flex", alignItems: "center", gap: "10px", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={editForm.is_active}
                      onChange={e => setEditForm(f => ({ ...f, is_active: e.target.checked }))}
                      style={{ accentColor: "#1faa89", width: "16px", height: "16px" }}
                    />
                    <span style={{ color: "#e9fff7", fontSize: "14px" }}>Активен</span>
                  </label>
                  {editError && <span style={{ color: "#ef4444", fontSize: "13px" }}>{editError}</span>}
                </div>
                <DialogFooter>
                  <DialogClose asChild>
                    <button style={btnSecondary}>Отмена</button>
                  </DialogClose>
                  <button onClick={handleEdit} disabled={isEditing} style={{ ...btnPrimary, opacity: isEditing ? 0.7 : 1 }}>
                    <Save size={14} />
                    {isEditing ? "Сохранение..." : "Сохранить"}
                  </button>
                </DialogFooter>
              </DialogContent>
            </Dialog>

            {/* ── AlertDialog удаления ─────────────────────────────────────── */}
            <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
              <AlertDialogContent style={{ backgroundColor: "#071b19", border: "1px solid #04594D" }}>
                <AlertDialogHeader>
                  <AlertDialogTitle style={{ color: "#e9fff7" }}>Удалить пользователя?</AlertDialogTitle>
                  <AlertDialogDescription style={{ color: "#9ca3af" }}>
                    Пользователь{" "}
                    <b style={{ color: "#e9fff7" }}>{deleteTarget?.name}</b>{" "}
                    ({deleteTarget?.email}) будет удалён без возможности восстановления.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel style={btnSecondary}>Отмена</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={handleDelete}
                    disabled={isDeleting}
                    style={{
                      backgroundColor: "#ef4444", color: "#fff", border: "none",
                      borderRadius: "8px", padding: "9px 18px",
                      fontSize: "14px", fontWeight: 600, cursor: "pointer",
                      opacity: isDeleting ? 0.7 : 1,
                    }}
                  >
                    {isDeleting ? "Удаление..." : "Удалить"}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
