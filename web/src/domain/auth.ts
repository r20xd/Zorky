import type { Me, Role } from "./types";

// Экран веба. master — путь /master. Роль null = режим разработки (бэкенд открыт): можно всё.
export type Screen =
  | "monitor"
  | "queue"
  | "master"
  | "areas"
  | "zones"
  | "analytics"
  | "journal"
  | "cameras"
  | "users"
  | "linq"
  | "demo";
export type Access = Role | null;

export const SCREEN_LABELS: Record<Screen, string> = {
  monitor: "Мониторинг",
  queue: "Требуют решения",
  master: "Мастер",
  areas: "Участки",
  zones: "Зоны",
  analytics: "Аналитика",
  journal: "Журнал",
  cameras: "Камеры",
  users: "Пользователи",
  linq: "LinQ",
  demo: "Демо-видео",
};

const ROLES: readonly Role[] = ["operator", "master", "specialist", "manager", "auditor", "admin"];

/** Разбор ответа /api/auth/me: неполный или с неизвестной ролью — null. */
export function parseMe(data: unknown): Me | null {
  if (typeof data !== "object" || data === null) return null;
  const { login, name, role } = data as Record<string, unknown>;
  if (typeof login !== "string" || typeof name !== "string") return null;
  if (!ROLES.includes(role as Role)) return null;
  const must = (data as Record<string, unknown>).must_change_password;
  const rawAreas = (data as Record<string, unknown>).areas;
  const areas = Array.isArray(rawAreas) ? rawAreas.filter((a): a is string => typeof a === "string") : [];
  const me: Me = { login, name, role: role as Role };
  if (must === true) me.must_change_password = true;
  if (areas.length) me.areas = areas;
  return me;
}

export function homeScreen(role: Access): Screen {
  switch (role) {
    case "master":
      return "master";
    case "specialist":
      return "areas";
    case "manager":
      return "analytics";
    case "auditor":
      return "journal";
    case "admin":
      return "cameras";
    default:
      return "monitor";
  }
}

export function allowedScreens(role: Access): Screen[] {
  switch (role) {
    case "operator":
      return ["monitor", "queue", "journal"];
    case "master":
      return ["master", "journal"];
    case "specialist":
      return ["areas", "zones", "journal", "analytics"];
    case "manager":
      return ["analytics", "journal", "linq"];
    case "auditor":
      return ["journal", "analytics"];
    case "admin":
      return ["cameras", "areas", "monitor", "zones", "users", "journal", "analytics", "linq", "demo"];
    default:
      return ["monitor", "queue", "master", "areas", "zones", "analytics", "journal", "cameras", "users", "linq", "demo"];
  }
}

/** Экран по адресу: /master — мастеру; иначе или без прав — домашний. */
export function screenForPath(path: string, role: Access): Screen {
  const wanted: Screen | null = path.replace(/\/+$/, "") === "/master" ? "master" : null;
  return wanted && allowedScreens(role).includes(wanted) ? wanted : homeScreen(role);
}

export const pathForScreen = (screen: Screen) => (screen === "master" ? "/master" : "/");

/** Разметка событий (PATCH): operator и master. */
export const canMark = (role: Access) => role === null || role === "operator" || role === "master";

/** Правка зон (PUT): specialist. */
export const canEditZones = (role: Access) => role === null || role === "specialist";

/** Экран только для просмотра: admin видит мониторинг и зоны, но не меняет (права бэкенда прежние). */
export const isViewOnly = (role: Access, screen: Screen) =>
  (role === "admin" && (screen === "monitor" || screen === "zones")) ||
  (role === "manager" && screen === "linq");

/** Участки, правила камер, новые камеры при вводе участка: specialist и admin. */
export const canManageAreas = (role: Access) => role === null || role === "specialist" || role === "admin";

/** Камеры (POST, DELETE) и адреса RTSP: admin. */
export const canManageCameras = (role: Access) => role === null || role === "admin";

/** Пользователи (/api/users): admin. */
export const canManageUsers = (role: Access) => role === null || role === "admin";

/** После входа: при временном пароле — только форма смены пароля. */
export const mustChangePassword = (me: Me | null) => me?.must_change_password === true;
