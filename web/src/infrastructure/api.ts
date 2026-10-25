import type {
  Area,
  Camera,
  EventStatus,
  FalseReason,
  Me,
  RuleSchedule,
  Role,
  User,
  UserCreateBody,
  ViolationEvent,
  Zone,
} from "../domain/types";
import { ApiError } from "../domain/actionError";
import { parseMe } from "../domain/auth";
import { eventsUrl, type JournalFilters } from "../domain/journal";

/** Сессия кончилась (401) — один обработчик на всё приложение: показать вход. */
let onUnauthorized: () => void = () => {};
export const setUnauthorizedHandler = (handler: () => void) => {
  onUnauthorized = handler;
};

/** 401 → на вход; 403 → «нет прав». */
function guard(response: Response) {
  if (response.status === 401) {
    onUnauthorized();
    throw new ApiError(401, "нужно войти");
  }
  if (response.status === 403) throw new ApiError(403, "нет прав");
}

// Все пути — относительные: в dev их проксирует Vite, в docker — nginx.
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  guard(response);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.error ?? `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export const listEvents = (limit = 100) =>
  request<ViolationEvent[]>(`/api/events?limit=${limit}`);

export const listCameras = () => request<Camera[]>("/api/cameras");

export const setEventStatus = (id: string, status: EventStatus, reason?: FalseReason) =>
  request<ViolationEvent>(`/api/events/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(reason ? { status, reason } : { status }),
  });

/**
 * Живая лента (SSE). Переподключается EventSource сам. Возвращает функцию отписки.
 * violation — новый инцидент, update — повтор или смена статуса, master — передан мастеру,
 * escalation — мастер не ответил (приходит с полем note).
 */
export type StreamEventName = "violation" | "master" | "update" | "escalation";
const OPERATOR_STREAM_EVENTS: StreamEventName[] = ["violation", "master", "update", "escalation"];

export function subscribeEvents(
  onEvent: (event: ViolationEvent, name: StreamEventName) => void,
  onStatus: (online: boolean) => void,
): () => void {
  const source = new EventSource("/api/stream");
  for (const name of OPERATOR_STREAM_EVENTS) {
    source.addEventListener(name, (message) => {
      onEvent(JSON.parse((message as MessageEvent<string>).data), name);
    });
  }
  source.onopen = () => onStatus(true);
  source.onerror = () => onStatus(false);
  return () => source.close();
}

export const getZones = (cameraId: string) =>
  request<Zone[]>(`/api/cameras/${encodeURIComponent(cameraId)}/zones`);

export const putZones = (cameraId: string, zones: Zone[]) =>
  request<Zone[]>(`/api/cameras/${encodeURIComponent(cameraId)}/zones`, {
    method: "PUT",
    body: JSON.stringify(zones),
  });

export const getMetrics = () => request<unknown>("/api/metrics");

/** Демо-видео (только демо-режим): свои ролики по кругу как камеры. */
export const getDemoVideos = () => request<unknown>("/api/demo/videos");

export async function uploadDemoVideo(file: File): Promise<unknown> {
  const response = await fetch(`/api/demo/videos?name=${encodeURIComponent(file.name)}`, {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: file,
  });
  guard(response);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, body.error ?? `${response.status} ${response.statusText}`);
  return body;
}

export const playDemoVideo = (cameraId: string, video: string, detect = false) =>
  request<unknown>("/api/demo/play", {
    method: "POST",
    body: JSON.stringify(detect ? { camera_id: cameraId, video, detect: true } : { camera_id: cameraId, video }),
  });

export const stopDemoVideo = (cameraId: string) =>
  request<unknown>("/api/demo/stop", { method: "POST", body: JSON.stringify({ camera_id: cameraId }) });

export async function deleteDemoVideo(name: string): Promise<unknown> {
  const response = await fetch(`/api/demo/videos/${encodeURIComponent(name)}`, { method: "DELETE" });
  guard(response);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.error ?? `${response.status} ${response.statusText}`);
  }
  return getDemoVideos();
}

/** Подключение к LinQ (раздел «LinQ»): статус, подключить, отключить, выбрать устройство. */
export const getLinq = () => request<unknown>("/api/linq");
export const linqConnect = () => request<unknown>("/api/linq/connect", { method: "POST" });
export const linqDisconnect = () => request<unknown>("/api/linq/disconnect", { method: "POST" });
export const setLinqDevice = (device: string) =>
  request<unknown>("/api/linq/config", { method: "PUT", body: JSON.stringify({ device }) });

/** Страница журнала с фильтрами; общее число — из заголовка X-Total-Count. */
export async function fetchJournal(
  filters: JournalFilters,
  limit: number,
  offset: number,
): Promise<{ events: ViolationEvent[]; total: number }> {
  const response = await fetch(eventsUrl(filters, { limit, offset }));
  guard(response);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  const events = (await response.json()) as ViolationEvent[];
  const total = Number(response.headers.get("X-Total-Count") ?? events.length);
  return { events, total: Number.isNaN(total) ? events.length : total };
}

/** Кто вошёл: me — пользователь; dev — бэкенд без входа (режим разработки: /me нет); null — не вошёл. */
export async function fetchMe(): Promise<{ me: Me | null; dev: boolean }> {
  const response = await fetch("/api/auth/me");
  if (response.status === 401) return { me: null, dev: false };
  if (response.status === 404) return { me: null, dev: true };
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  const me = parseMe(await response.json());
  // 200 без разбираемой роли — тоже режим разработки (бэкенд открыт, роли нет)
  return { me, dev: me === null };
}

export async function login(loginName: string, password: string): Promise<void> {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login: loginName, password }),
  });
  if (response.status === 401) throw new Error("неверный логин или пароль");
  if (response.status === 429) {
    const wait = response.headers.get("Retry-After");
    throw new Error(`слишком много попыток${wait ? `, повторите через ${wait} с` : ""}`);
  }
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
}

/** Роли демо-режима для входа в один клик; вне демо — пустой список. */
export async function getDemoUsers(): Promise<{ login: string; name: string; role: string }[]> {
  try {
    const response = await fetch("/api/auth/demo");
    if (!response.ok) return [];
    const data: unknown = await response.json();
    return Array.isArray(data)
      ? data.filter((u) => u && typeof u.login === "string" && typeof u.name === "string" && typeof u.role === "string")
      : [];
  } catch {
    return [];
  }
}

export async function demoLogin(loginName: string): Promise<void> {
  const response = await fetch("/api/auth/demo", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login: loginName }),
  });
  if (!response.ok) throw new Error(`демо-вход не удался: ${response.status}`);
}

export const logout = () => fetch("/api/auth/logout", { method: "POST" }).then(() => undefined);

/** Очередь мастера: инциденты sent_to_master. */
export const listMasterEvents = () => request<ViolationEvent[]>("/api/events?status=sent_to_master&limit=200");

/** SSE для мастера: `master` — новая карточка, `update` — смена статуса и повторы. onStatus(false) — обрыв. */
export function subscribeMaster(
  onMaster: (event: ViolationEvent) => void,
  onStatus: (online: boolean) => void,
  onUpdate: (event: ViolationEvent) => void = () => undefined,
): () => void {
  const source = new EventSource("/api/stream");
  source.addEventListener("master", (message) => {
    onMaster(JSON.parse((message as MessageEvent<string>).data));
  });
  // решили или отметили ложным на другом устройстве, выросло число повторов
  source.addEventListener("update", (message) => {
    onUpdate(JSON.parse((message as MessageEvent<string>).data));
  });
  source.onopen = () => onStatus(true);
  source.onerror = () => onStatus(false);
  return () => source.close();
}

/** Добавить или обновить камеру (POST — по id), только admin. */
export const listAreas = () => request<Area[]>("/api/areas");

/** Создать участок или сменить его правила; rules: null — набор по умолчанию. */
export const putArea = (name: string, rules: string[] | null, ruleSchedule?: RuleSchedule | null) =>
  request<Area>(`/api/areas/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify(ruleSchedule ? { rules, rule_schedule: ruleSchedule } : { rules }),
  });

export async function deleteArea(name: string): Promise<void> {
  const response = await fetch(`/api/areas/${encodeURIComponent(name)}`, { method: "DELETE" });
  guard(response);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.error ?? `${response.status} ${response.statusText}`);
  }
}

/** Камера: на участок, переименовать, свои правила (null — вернуть правила участка). */
export const patchCamera = (
  id: string,
  body: { name?: string; area?: string | null; rules?: string[] | null; rule_schedule?: RuleSchedule | null },
) =>
  request<Camera>(`/api/cameras/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) });

export const saveCamera = (camera: Camera) =>
  request<Camera>("/api/cameras", { method: "POST", body: JSON.stringify(camera) });

export async function deleteCamera(id: string): Promise<void> {
  const response = await fetch(`/api/cameras/${encodeURIComponent(id)}`, { method: "DELETE" });
  guard(response);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
}

export const listUsers = () => request<User[]>("/api/users");

/** temp_password — только если пароль сгенерировал бэкенд. */
export const createUser = (body: UserCreateBody) =>
  request<{ user: User; temp_password: string | null }>("/api/users", { method: "POST", body: JSON.stringify(body) });

export const patchUser = (login: string, body: { name?: string; role?: Role; active?: boolean; areas?: string[] }) =>
  request<User>(`/api/users/${encodeURIComponent(login)}`, { method: "PATCH", body: JSON.stringify(body) });

export const resetPassword = (login: string, own?: { password: string; must_change_password: boolean }) =>
  request<{ temp_password: string | null }>(`/api/users/${encodeURIComponent(login)}/reset-password`, {
    method: "POST",
    body: own ? JSON.stringify(own) : undefined,
  });

/** Смена своего пароля (204 без тела). */
export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  const response = await fetch("/api/auth/password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
  guard(response);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.error ?? `${response.status} ${response.statusText}`);
  }
}
