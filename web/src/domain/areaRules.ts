import { DEFAULT_RULES, RULE_INFO, RULE_TYPES } from "./ruleCatalog";
import type { Area, Camera, RuleSchedule, RuleWindow, ViolationType, Zone, ZoneKind } from "./types";

export type RulesSource = "camera" | "area" | "default";

/** Какие правила действуют на камере: свои, иначе участка, иначе по умолчанию. */
export function effectiveRules(
  camera: Camera,
  area?: Area,
): { enabled: ViolationType[]; source: RulesSource } {
  if (camera.rules) return { enabled: knownRules(camera.rules), source: "camera" };
  if (area?.rules) return { enabled: knownRules(area.rules), source: "area" };
  return { enabled: DEFAULT_RULES, source: "default" };
}

/** Правила участка: свои или набор по умолчанию. */
export const areaRules = (area: Area): ViolationType[] =>
  area.rules ? knownRules(area.rules) : DEFAULT_RULES;

/** Коды из бэкенда → только известные вебу, в порядке показа. */
const knownRules = (codes: string[]): ViolationType[] => RULE_TYPES.filter((type) => codes.includes(type));

/** Включить или выключить правило; порядок остаётся порядком показа. */
export function toggleRule(list: ViolationType[], type: ViolationType): ViolationType[] {
  const next = list.includes(type) ? list.filter((item) => item !== type) : [...list, type];
  return RULE_TYPES.filter((item) => next.includes(item));
}

/** Включённые правила, которым нужна зона такого вида, а на камере её нет. */
export function missingZones(
  enabled: ViolationType[],
  zones: Zone[],
): { type: ViolationType; kind: ZoneKind }[] {
  const have = new Set(zones.map((zone) => zone.kind));
  return enabled.flatMap((type) => {
    const kind = RULE_INFO[type].zoneKind;
    return kind && !have.has(kind) ? [{ type, kind }] : [];
  });
}

export const DEFAULT_WINDOW: RuleWindow = { start: "08:00", end: "17:00" };
const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;

/** Включить правило по расписанию (window) или снять расписание (null — правило действует всегда). */
export function setRuleWindow(
  schedule: RuleSchedule,
  type: ViolationType,
  window: RuleWindow | null,
): RuleSchedule {
  const { [type]: _old, ...rest } = schedule;
  return window ? { ...rest, [type]: window } : rest;
}

/** Расписание для отправки: только включённые правила; null, если окон нет. */
export function cleanSchedule(enabled: ViolationType[], schedule: RuleSchedule): RuleSchedule | null {
  const out = Object.fromEntries(Object.entries(schedule).filter(([type]) => enabled.includes(type as ViolationType)));
  return Object.keys(out).length ? out : null;
}

/** Проблема в окнах расписания (формат ЧЧ:ММ); null — всё хорошо. */
export function scheduleProblem(schedule: RuleSchedule): string | null {
  for (const [type, w] of Object.entries(schedule)) {
    if (!CLOCK.test(w.start) || !CLOCK.test(w.end)) return `${type}: время в формате ЧЧ:ММ`;
  }
  return null;
}

/** «08:00–17:00» или «всегда». */
export const windowLabel = (w?: RuleWindow): string => (w ? `${w.start}–${w.end}` : "всегда");

/** Одинаковы ли расписания (для «есть несохранённое»). */
export const sameSchedule = (a?: RuleSchedule, b?: RuleSchedule): boolean =>
  JSON.stringify(a ?? {}) === JSON.stringify(b ?? {});

/** Следующий свободный id камеры: cam-1, cam-2… */
export function nextCameraId(cameras: Camera[]): string {
  const used = new Set(cameras.map((camera) => camera.id));
  let n = cameras.length + 1;
  while (used.has(`cam-${n}`)) n++;
  return `cam-${n}`;
}

/** Название нового участка: не пустое, не длиннее 64, без повторов (без учёта регистра). */
export function validateAreaName(name: string, areas: Area[]): string | null {
  const trimmed = name.trim();
  if (!trimmed) return "укажите название участка";
  if (trimmed.length > 64) return "название участка — до 64 символов";
  if (areas.some((area) => area.name.toLowerCase() === trimmed.toLowerCase())) return "такой участок уже есть";
  return null;
}

/** «9 из 18»: сколько правил контролируется. */
export const rulesSummary = (enabled: ViolationType[]): string => `${enabled.length} из ${RULE_TYPES.length}`;

export const SOURCE_LABELS: Record<RulesSource, string> = {
  camera: "свои правила камеры",
  area: "как на участке",
  default: "по умолчанию",
};

/** Камера этого компьютера: воркер открывает её по номеру. */
export const WEBCAM_SOURCE = "0";
