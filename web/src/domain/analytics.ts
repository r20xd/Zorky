import { VIOLATION_LABELS, type ViolationEvent, type ViolationType } from "./types";

export interface Analytics {
  total: number;
  byType: Record<ViolationType, number>;
  byCamera: Record<string, number>;
  /** 24 корзины: час суток (локальное время) 0..23 */
  byHour: number[];
  byZone: Record<string, number>;
  /** ключ — дата YYYY-MM-DD (локальная), по возрастанию */
  byDay: Record<string, number>;
  byShift: Record<ShiftId, number>;
  /** доля ложных 0..1 среди всех событий */
  falseShare: number;
  /** среднее, мс: first_seen → передано мастеру; null — данных нет */
  avgToMasterMs: number | null;
  /** среднее, мс: передано мастеру → решено */
  avgToResolvedMs: number | null;
}

export type ShiftId = "night" | "day" | "evening";
export const SHIFT_LABELS: Record<ShiftId, string> = {
  night: "Ночная 00–08",
  day: "Дневная 08–16",
  evening: "Вечерняя 16–24",
};
export const shiftOf = (hour: number): ShiftId => (hour < 8 ? "night" : hour < 16 ? "day" : "evening");

const NO_ZONE = "без зоны";
const pad = (n: number) => String(n).padStart(2, "0");
const avg = (xs: number[]) => (xs.length ? xs.reduce((s, x) => s + x, 0) / xs.length : null);
const firstTs = (e: ViolationEvent, status: string) => {
  const c = e.status_history.find((h) => h.status === status);
  return c ? Date.parse(c.ts) : NaN;
};

/** Чистый подсчёт нарушений по типам, камерам и часам суток. События с битым ts в часы не попадают. */
export function computeAnalytics(events: ViolationEvent[]): Analytics {
  const byType = Object.fromEntries(
    (Object.keys(VIOLATION_LABELS) as ViolationType[]).map((t) => [t, 0]),
  ) as Record<ViolationType, number>;
  const byCamera: Record<string, number> = {};
  const byHour = new Array<number>(24).fill(0);
  const byZone: Record<string, number> = {};
  const byDay: Record<string, number> = {};
  const byShift: Record<ShiftId, number> = { night: 0, day: 0, evening: 0 };
  const toMaster: number[] = [];
  const toResolved: number[] = [];
  let falseCount = 0;
  for (const event of events) {
    const zone = event.zone_id || NO_ZONE;
    byZone[zone] = (byZone[zone] ?? 0) + 1;
    if (event.status === "false") falseCount += 1;
    const sent = firstTs(event, "sent_to_master");
    const done = firstTs(event, "resolved");
    const seen = Date.parse(event.first_seen);
    if (!Number.isNaN(sent - seen)) toMaster.push(Math.max(0, sent - seen));
    if (!Number.isNaN(done - sent)) toResolved.push(Math.max(0, done - sent));
    if (event.type in byType) byType[event.type] += 1;
    byCamera[event.camera_id] = (byCamera[event.camera_id] ?? 0) + 1;
    const date = new Date(event.ts);
    if (!Number.isNaN(date.getTime())) {
      byHour[date.getHours()] += 1;
      byShift[shiftOf(date.getHours())] += 1;
      const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
      byDay[day] = (byDay[day] ?? 0) + 1;
    }
  }
  const sortedDays = Object.fromEntries(Object.entries(byDay).sort(([a], [b]) => a.localeCompare(b)));
  return {
    total: events.length,
    byType,
    byCamera,
    byHour,
    byZone,
    byDay: sortedDays,
    byShift,
    falseShare: events.length ? falseCount / events.length : 0,
    avgToMasterMs: avg(toMaster),
    avgToResolvedMs: avg(toResolved),
  };
}

export type Period = "24h" | "7d" | "all";
export const PERIOD_LABELS: Record<Period, string> = {
  "24h": "Последние 24 часа",
  "7d": "Последние 7 дней",
  all: "Всё время",
};
const PERIOD_MS: Record<Period, number> = { "24h": 86_400_000, "7d": 7 * 86_400_000, all: Infinity };

export interface AnalyticsFilter {
  camera: string;
  zone: string;
  period: Period;
  shift: ShiftId | "all";
}
export const ALL = "all";
export const NO_FILTER: AnalyticsFilter = { camera: ALL, zone: ALL, period: "all", shift: "all" };

/** Фильтр событий: камера, зона, период от now и смена (по часу локального времени). Битый ts — отбрасываем. */
export function filterEvents(events: ViolationEvent[], f: AnalyticsFilter, now: number): ViolationEvent[] {
  return events.filter((e) => {
    if (f.camera !== ALL && e.camera_id !== f.camera) return false;
    if (f.zone !== ALL && (e.zone_id || NO_ZONE) !== f.zone) return false;
    const t = Date.parse(e.ts);
    if (Number.isNaN(t)) return false;
    if (now - t > PERIOD_MS[f.period]) return false;
    return f.shift === ALL || shiftOf(new Date(t).getHours()) === f.shift;
  });
}

export interface TypeShare {
  type: ViolationType;
  count: number;
  /** доля 0..1 от всех нарушений в выборке */
  share: number;
}

/** Нарушения по типам с долями; сортировка по убыванию, типы без нарушений — в конце. */
export function typeShares(events: ViolationEvent[]): TypeShare[] {
  const a = computeAnalytics(events);
  const total = a.total || 1;
  return (Object.keys(VIOLATION_LABELS) as ViolationType[])
    .map((type) => ({ type, count: a.byType[type], share: a.byType[type] / total }))
    .sort((x, y) => y.count - x.count);
}

/** Час суток с наибольшим числом нарушений; null — нарушений нет. */
export function peakHour(byHour: number[]): number | null {
  const max = Math.max(0, ...byHour);
  return max === 0 ? null : byHour.indexOf(max);
}

/** Уникальные зоны событий для селектора. */
export function zonesOf(events: ViolationEvent[]): string[] {
  return [...new Set(events.map((e) => e.zone_id || NO_ZONE))].sort();
}
