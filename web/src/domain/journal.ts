import type { EventStatus, StatusChange, ViolationEvent, ViolationType } from "./types";

/** Фильтры журнала; пустая строка — «без фильтра». from/to — значения datetime-local (локальное время). */
export interface JournalFilters {
  type: ViolationType | "";
  camera: string;
  zone: string;
  status: EventStatus | "";
  from: string;
  to: string;
}

export const EMPTY_FILTERS: JournalFilters = {
  type: "",
  camera: "",
  zone: "",
  status: "",
  from: "",
  to: "",
};

export const PAGE_SIZE = 50;

function toIso(local: string): string | null {
  if (!local) return null;
  const date = new Date(local);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

/** Query-строка запроса журнала (без «?»): только заполненные фильтры. Без limit/offset при paging=null. */
export function buildQuery(
  filters: JournalFilters,
  paging: { limit: number; offset: number } | null = null,
): string {
  const params = new URLSearchParams();
  if (filters.type) params.set("type", filters.type);
  if (filters.camera) params.set("camera", filters.camera);
  if (filters.zone.trim()) params.set("zone", filters.zone.trim());
  if (filters.status) params.set("status", filters.status);
  const from = toIso(filters.from);
  const to = toIso(filters.to);
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  if (paging) {
    params.set("limit", String(paging.limit));
    params.set("offset", String(paging.offset));
  }
  return params.toString();
}

export const eventsUrl = (filters: JournalFilters, paging: { limit: number; offset: number }) =>
  `/api/events?${buildQuery(filters, paging)}`;

export function csvUrl(filters: JournalFilters): string {
  const query = buildQuery(filters);
  return query ? `/api/events.csv?${query}` : "/api/events.csv";
}

/** Первая реакция человека: смена статуса не системой (автопередача мастеру реакцией не считается). */
export const reactionChange = (event: ViolationEvent): StatusChange | undefined =>
  event.status_history.find((change) => change.status !== "new" && change.by !== "system");

/** Время реакции, мс: от появления инцидента до первой реакции человека; null — реакции не было. */
export function reactionMs(event: ViolationEvent): number | null {
  const first = reactionChange(event);
  if (!first) return null;
  const ms = Date.parse(first.ts) - Date.parse(event.first_seen);
  return Number.isNaN(ms) ? null : Math.max(0, ms);
}

export function formatDuration(ms: number | null): string {
  if (ms === null) return "—";
  const sec = Math.round(ms / 1000);
  if (sec < 60) return `${sec} с`;
  const min = Math.floor(sec / 60);
  return min < 60 ? `${min} мин ${sec % 60} с` : `${Math.floor(min / 60)} ч ${min % 60} мин`;
}

/** Строки истории: время реакции каждой смены считается от first_seen инцидента. */
export function historyRows(event: ViolationEvent): { change: StatusChange; afterMs: number }[] {
  return event.status_history.map((change) => ({
    change,
    afterMs: Math.max(0, Date.parse(change.ts) - Date.parse(event.first_seen)),
  }));
}

export function pageCount(total: number, size = PAGE_SIZE): number {
  return Math.max(1, Math.ceil(total / size));
}
