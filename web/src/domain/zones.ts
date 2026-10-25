import { ZONE_KINDS, ZONE_KIND_LIST } from "./zoneKinds";
import type { Point, Zone, ZoneKind, ZoneSchedule } from "./types";

const clamp01 = (value: number) => Math.min(1, Math.max(0, value));

/** Клик в пикселях внутри рамки кадра → точка 0..1. */
export function toNormalized(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number; width: number; height: number },
): Point {
  if (rect.width <= 0 || rect.height <= 0) return [0, 0];
  return [clamp01((clientX - rect.left) / rect.width), clamp01((clientY - rect.top) / rect.height)];
}

/** Точки полигона → строка для SVG polygon в viewBox 0 0 1 1. */
export const pointsAttr = (polygon: Point[]) => polygon.map(([x, y]) => `${x},${y}`).join(" ");

/** Следующий свободный id вида z-N. */
export function nextZoneId(zones: Zone[]): string {
  const used = new Set(zones.map((zone) => zone.id));
  let n = zones.length + 1;
  while (used.has(`z-${n}`)) n++;
  return `z-${n}`;
}

/** Рисуемый полигон закрыт в зону, если в нём ≥ 3 точек (контракт: minItems 3). */
export function finishZone(
  zones: Zone[],
  draft: Point[],
  kind: ZoneKind,
  name?: string,
  schedule?: ZoneSchedule,
): Zone[] {
  if (draft.length < 3) return zones;
  const zone: Zone = { id: nextZoneId(zones), kind, polygon: draft };
  if (name?.trim()) zone.name = name.trim();
  if (schedule) zone.schedule = schedule;
  return [...zones, zone];
}

const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;

/** Расписание зоны: оба времени в формате ЧЧ:ММ и не одинаковые (иначе это «всегда»). */
export function validateSchedule(schedule: ZoneSchedule): string | null {
  if (!CLOCK.test(schedule.start) || !CLOCK.test(schedule.end)) return "время зоны: ЧЧ:ММ";
  if (schedule.start === schedule.end) return "начало и конец расписания совпадают — снимите расписание";
  return null;
}

/** «07:55–12:00» или «всегда». */
export const scheduleLabel = (schedule?: ZoneSchedule): string =>
  schedule ? `${schedule.start}–${schedule.end}` : "всегда";

/** Расписание зоны; undefined снимает его. */
export const setZoneSchedule = (zones: Zone[], id: string, schedule?: ZoneSchedule): Zone[] =>
  zones.map((zone) => {
    if (zone.id !== id) return zone;
    const { schedule: _old, ...rest } = zone;
    return schedule ? { ...rest, schedule } : rest;
  });

export const removeZone = (zones: Zone[], id: string) => zones.filter((zone) => zone.id !== id);

/** Цвет обводки зоны на кадре по типу. */
export const ZONE_COLORS = Object.fromEntries(
  ZONE_KIND_LIST.map((k) => [k, ZONE_KINDS[k].color]),
) as Record<ZoneKind, string>;

/** Полупрозрачная заливка того же цвета. */
export const zoneFill = (kind: ZoneKind) => ZONE_COLORS[kind] + "4d";

export const setZoneKind = (zones: Zone[], id: string, kind: ZoneKind) =>
  zones.map((zone) => (zone.id === id ? { ...zone, kind } : zone));

/** Центр полигона (среднее вершин): сюда ставится подпись на кадре. */
export function centroid(polygon: Point[]): Point {
  const n = polygon.length || 1;
  return [polygon.reduce((s, [x]) => s + x, 0) / n, polygon.reduce((s, [, y]) => s + y, 0) / n];
}

/** Клик в пределах `px` пикселей от первой точки черновика (если точек уже ≥ 3) замыкает зону. */
export function isNearFirst(
  draft: Point[],
  click: Point,
  size: { width: number; height: number },
  px = 14,
): boolean {
  if (draft.length < 3) return false;
  const dx = (click[0] - draft[0][0]) * size.width;
  const dy = (click[1] - draft[0][1]) * size.height;
  return Math.hypot(dx, dy) <= px;
}

/** Название по умолчанию: «Опасная зона 2» — по виду и числу зон этого вида. */
export function defaultZoneName(zones: Zone[], kind: ZoneKind): string {
  return `${ZONE_KINDS[kind].label} ${zones.filter((zone) => zone.kind === kind).length + 1}`;
}

/** Зоны отличаются от сохранённых (для «есть несохранённые изменения»). */
export const zonesChanged = (a: Zone[], b: Zone[]): boolean => JSON.stringify(a) !== JSON.stringify(b);
