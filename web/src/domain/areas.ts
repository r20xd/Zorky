import type { Camera, Role, UserCreateBody, ViolationEvent } from "./types";

/** «Цех 1, Цех 2» → ["Цех 1", "Цех 2"]: запятая/перенос, без пустых и повторов. */
export function parseAreas(text: string): string[] {
  const out: string[] = [];
  for (const part of text.split(/[,;\n]/)) {
    const area = part.trim();
    if (area && !out.includes(area)) out.push(area);
  }
  return out;
}

export const areasText = (areas: string[] | undefined): string => (areas ?? []).join(", ");

/** Камера для отправки: пустой участок поля не создаёт. */
export function buildCameraBody(form: Camera): Camera {
  const body: Camera = { id: form.id.trim(), name: form.name.trim(), source: form.source?.trim() };
  const area = form.area?.trim();
  if (area) body.area = area;
  return body;
}

/** Тело создания пользователя: areas — только у мастера и только непустые. */
export function buildUserCreate(form: {
  login: string;
  name: string;
  role: Role;
  areas: string;
  password?: string;
  mustChange?: boolean;
}): UserCreateBody {
  const body: UserCreateBody = {
    login: form.login.trim(),
    name: form.name.trim(),
    role: form.role,
  };
  const areas = parseAreas(form.areas);
  if (form.role === "master" && areas.length) body.areas = areas;
  if (form.password) {
    body.password = form.password;
    body.must_change_password = form.mustChange ?? true;
  }
  return body;
}

/** PATCH участков: пустой список снимает ограничение (контракт), поэтому `[]` отправляем. */
export const buildAreasPatch = (text: string): { areas: string[] } => ({ areas: parseAreas(text) });

/** «Мои участки: Цех 1, Цех 2» или «Мои участки: все». */
export const myAreasLabel = (areas: string[] | undefined): string =>
  `Мои участки: ${areas && areas.length ? areas.join(", ") : "все"}`;

export const cameraArea = (cameras: Camera[], cameraId: string): string | undefined =>
  cameras.find((c) => c.id === cameraId)?.area || undefined;

export const eventArea = (event: ViolationEvent, cameras: Camera[]) => cameraArea(cameras, event.camera_id);

/** Список участков для фильтра: уникальные, по алфавиту. */
export function areaOptions(cameras: Camera[]): string[] {
  return [...new Set(cameras.map((c) => c.area).filter((a): a is string => !!a))].sort((a, b) =>
    a.localeCompare(b, "ru"),
  );
}

/** Фильтр по участку камеры события; пустой участок — без фильтра. */
export function filterByArea(events: ViolationEvent[], cameras: Camera[], area: string): ViolationEvent[] {
  return area ? events.filter((e) => eventArea(e, cameras) === area) : events;
}

/** « · Участок» для подписи инцидента; нет участка — пусто. */
export function areaSuffix(event: ViolationEvent, cameras: Camera[]): string {
  const area = eventArea(event, cameras);
  return area ? ` · ${area}` : "";
}
