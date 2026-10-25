import { plural } from "./plural";
import { VIOLATION_LABELS, type EventStatus, type Severity, type ViolationEvent, type ViolationType } from "./types";

/** Новое событие сверху, без дублей по id и инциденту (повтор обновляет карточку и счётчик), не больше limit штук. */
export function mergeEvent(
  list: ViolationEvent[],
  event: ViolationEvent,
  limit = 200,
): ViolationEvent[] {
  return [event, ...list.filter((item) => item.id !== event.id && item.incident_id !== event.incident_id)].slice(0, limit);
}

/** Событие из ленты: есть в списке — обновляем на месте (статус, счётчик), нет — как новое. */
export function upsertEvent(list: ViolationEvent[], event: ViolationEvent): ViolationEvent[] {
  return list.some((item) => item.id === event.id)
    ? replaceEvent(list, event)
    : mergeEvent(list, event);
}

/** Заменяет событие с тем же id — после разметки оператором. */
export function replaceEvent(list: ViolationEvent[], event: ViolationEvent): ViolationEvent[] {
  return list.map((item) => (item.id === event.id ? event : item));
}

/** Сколько событий каждого типа — для счётчиков над лентой; ложные срабатывания не считаются. */
export function countByType(list: ViolationEvent[]): Record<ViolationType, number> {
  const counts = Object.fromEntries(
    (Object.keys(VIOLATION_LABELS) as ViolationType[]).map((t) => [t, 0]),
  ) as Record<ViolationType, number>;
  for (const event of list) if (event.status !== "false") counts[event.type] += 1;
  return counts;
}

/** Время события по-русски, ЧЧ:ММ:СС. */
export function formatTime(ts: string): string {
  return new Date(ts).toLocaleTimeString("ru-RU", { hour12: false });
}

/** bbox [x1,y1,x2,y2] (0..1) в CSS-проценты для абсолютного позиционирования поверх кадра. */
export function bboxStyle(bbox: [number, number, number, number]) {
  const [x1, y1, x2, y2] = bbox;
  const clamp = (v: number) => Math.min(1, Math.max(0, v));
  const left = clamp(Math.min(x1, x2));
  const top = clamp(Math.min(y1, y2));
  return {
    left: `${left * 100}%`,
    top: `${top * 100}%`,
    width: `${(clamp(Math.max(x1, x2)) - left) * 100}%`,
    height: `${(clamp(Math.max(y1, y2)) - top) * 100}%`,
  };
}

/**
 * Рамки, которые ещё показываем на камере: события этой камеры, пришедшие не раньше ttl мс назад
 * (по локальному времени прихода seen[id]), без ложных срабатываний.
 */
export function liveBoxes(
  list: ViolationEvent[],
  seen: Record<string, number>,
  cameraId: string,
  now: number,
  ttlMs = 3000,
): ViolationEvent[] {
  return list.filter(
    (e) =>
      e.camera_id === cameraId &&
      e.status !== "false" &&
      e.id in seen &&
      now - seen[e.id] <= ttlMs,
  );
}

/** Сколько активных рамок у каждой камеры — для значка на плитке. */
export function violatingCameras(
  list: ViolationEvent[],
  seen: Record<string, number>,
  now: number,
  ttlMs = 3000,
): Set<string> {
  return new Set(
    list.filter((e) => liveBoxes([e], seen, e.camera_id, now, ttlMs).length).map((e) => e.camera_id),
  );
}

/** Подпись под камерой: сколько неразобранных нарушений (new / sent_to_master) или «Нарушений нет». */
export function openViolationsLabel(list: ViolationEvent[], cameraId: string): string {
  const n = list.filter(
    (e) => e.camera_id === cameraId && (e.status === "new" || e.status === "sent_to_master"),
  ).length;
  if (!n) return "Нарушений нет";
  return `Открыто ${n} ${plural(n, ["нарушение", "нарушения", "нарушений"])}`;
}

/** Адрес снимка нарушения или null, если снимка нет. */
export function snapshotSrc(event: ViolationEvent): string | null {
  const url = event.snapshot_url?.trim();
  return url ? url : null;
}

/** Критичное событие: падение, неподвижность (и всё с severity critical). */
export function isCritical(event: ViolationEvent): boolean {
  return event.type === "fall" || event.type === "immobile" || event.severity === "critical";
}

/** Критичные сверху, внутри группы — свежие (по last_seen) выше; порядок стабильный. */
export function sortFeed(list: ViolationEvent[]): ViolationEvent[] {
  return list
    .map((event, i) => ({ event, i }))
    .sort((a, b) => {
      const ca = isCritical(a.event) ? 1 : 0;
      const cb = isCritical(b.event) ? 1 : 0;
      if (ca !== cb) return cb - ca;
      const ta = Date.parse(a.event.last_seen) || 0;
      const tb = Date.parse(b.event.last_seen) || 0;
      return tb - ta || a.i - b.i;
    })
    .map((x) => x.event);
}

export const STATUS_LABELS: Record<EventStatus, string> = {
  new: "Новое",
  confirmed: "Подтверждено",
  sent_to_master: "Передано мастеру",
  resolved: "Решено",
  false: "Ложное",
};

/** Бейдж статуса: подпись и css-класс. */
export function statusBadge(status: EventStatus): { label: string; className: string } {
  return { label: STATUS_LABELS[status], className: `status status-${status}` };
}

export const SEVERITY_LABELS: Record<Severity, string> = {
  critical: "Критично",
  high: "Высокая",
  medium: "Средняя",
  low: "Низкая",
};

/** Критичность по типу, как в contracts/violation_types.json — если воркер не прислал severity. */
const TYPE_SEVERITY: Record<ViolationType, Severity> = {
  fall: "critical",
  immobile: "critical",
  vehicle_proximity: "critical",
  vehicle_collision: "critical",
  no_helmet: "high",
  danger_zone: "high",
  smoking: "high",
  phone: "high",
  fence_crossing: "high",
  under_load: "high",
  distress_signal: "high",
  lone_worker: "high",
  vehicle_in_zone: "high",
  no_respirator: "medium",
  overcrowding: "medium",
  at_height: "medium",
  loitering: "medium",
  running: "low",
};

/** Критичность для показа: падение и неподвижность всегда критичны, иначе — из события, иначе — по типу. */
export function severityOf(event: ViolationEvent): Severity {
  return isCritical(event) ? "critical" : (event.severity ?? TYPE_SEVERITY[event.type] ?? "low");
}

/** Начало инцидента для таймера: first_seen, а если бэкенд его не прислал — время события. */
export function startedAt(event: ViolationEvent): string {
  return event.first_seen || event.ts;
}

/** Сколько активных (не ложных) событий каждой критичности — сводка над лентой. */
export function countBySeverity(list: ViolationEvent[]): Record<Severity, number> {
  const counts: Record<Severity, number> = { critical: 0, high: 0, medium: 0, low: 0 };
  for (const event of list) if (event.status !== "false") counts[severityOf(event)] += 1;
  return counts;
}

export type WaitTone = "ok" | "warn" | "high" | "crit";

/** Цвет таймера ожидания: до 2 мин, 2–5, 5–15, дольше 15 мин. */
export function waitTone(sec: number): WaitTone {
  if (sec < 120) return "ok";
  if (sec < 300) return "warn";
  if (sec < 900) return "high";
  return "crit";
}

/** Сколько секунд прошло с начала инцидента; null, если время не разобрать. */
export function waitSeconds(since: string, now: number): number | null {
  const t = Date.parse(since);
  return Number.isNaN(t) ? null : Math.max(0, Math.floor((now - t) / 1000));
}

/** Время ожидания с начала инцидента: ММ:СС (часы — в минутах); пустая строка, если время не разобрать. */
export function waitClock(since: string, now: number): string {
  const sec = waitSeconds(since, now);
  if (sec === null) return "";
  return `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
}

/** Подпись счётчика повторов инцидента или null, если повторов нет. */
export function repeatLabel(event: ViolationEvent): string | null {
  return event.count > 1 ? `×${event.count}` : null;
}

/** Нужен ли звук: пришло критичное, которого мы ещё не слышали (новый инцидент или рост счётчика). */
export function shouldBeep(prev: ViolationEvent[], event: ViolationEvent): boolean {
  if (!isCritical(event) || event.status !== "new") return false;
  const old = prev.find((item) => item.incident_id === event.incident_id);
  return !old || event.count > old.count;
}
