import { ZONE_KINDS, ZONE_KIND_LIST } from "./zoneKinds";
// Типы контракта contracts/openapi.yaml. Меняются только вместе с контрактом.
export type ViolationType =
  | "no_helmet"
  | "no_respirator"
  | "danger_zone"
  | "smoking"
  | "fall"
  | "immobile"
  | "vehicle_proximity"
  | "running"
  | "phone"
  | "fence_crossing"
  | "under_load"
  | "distress_signal"
  | "lone_worker"
  | "overcrowding"
  | "at_height"
  | "loitering"
  | "vehicle_in_zone"
  | "vehicle_collision";
export type EventStatus = "new" | "confirmed" | "sent_to_master" | "resolved" | "false";
export type Severity = "low" | "medium" | "high" | "critical";

export type FalseReason = "not_person" | "ppe_present" | "wrong_zone" | "duplicate" | "other";

export interface StatusChange {
  status: EventStatus;
  ts: string;
  by: string;
  reason?: FalseReason;
}

export interface ViolationEvent {
  id: string;
  camera_id: string;
  ts: string;
  frame_id: number;
  type: ViolationType;
  bbox: [number, number, number, number];
  confidence: number;
  track_id: number;
  snapshot_url?: string;
  zone_id?: string;
  severity: Severity;
  incident_id: string;
  count: number;
  first_seen: string;
  last_seen: string;
  status: EventStatus;
  status_history: StatusChange[];
  received_at: string;
}

export type Role = "operator" | "master" | "specialist" | "manager" | "auditor" | "admin";

/** Тело создания пользователя админом. */
export interface UserCreateBody {
  login: string;
  name: string;
  role: Role;
  areas?: string[];
  /** свой пароль; нет — бэкенд сгенерирует временный */
  password?: string;
  must_change_password?: boolean;
}

export interface Me {
  login: string;
  name: string;
  role: Role;
  must_change_password?: boolean;
  areas?: string[];
}

export interface User {
  login: string;
  name: string;
  role: Role;
  active: boolean;
  created_at: string;
  created_by: string;
  areas?: string[];
}

/** Окно действия правила (местное время, «ЧЧ:ММ»); start > end — через полночь. */
export interface RuleWindow {
  start: string;
  end: string;
}

/** Код типа нарушения -> окно; правила без записи действуют всегда. */
export type RuleSchedule = Record<string, RuleWindow>;

export interface Camera {
  id: string;
  name: string;
  source?: string;
  area?: string;
  /** какие нарушения контролировать; нет — как на участке, а там нет — по умолчанию */
  rules?: string[];
  rule_schedule?: RuleSchedule;
}

/** Участок с общими правилами; cameras — id его камер (только в ответе). */
export interface Area {
  name: string;
  rules?: string[];
  rule_schedule?: RuleSchedule;
  cameras: string[];
}

export const VIOLATION_LABELS: Record<ViolationType, string> = {
  no_helmet: "Нет каски",
  no_respirator: "Нет респиратора",
  danger_zone: "Опасная зона",
  smoking: "Курение",
  fall: "Падение человека",
  immobile: "Человек неподвижен",
  vehicle_proximity: "Опасная близость к технике",
  running: "Бег на территории",
  phone: "Телефон в опасной зоне",
  fence_crossing: "Пересечение ограждения",
  under_load: "Под грузом или конвейером",
  distress_signal: "Сигнал бедствия",
  lone_worker: "Одиночная работа",
  overcrowding: "Превышение числа людей в зоне",
  at_height: "На высоте — проверьте страховку",
  loitering: "Долгое нахождение в опасной зоне",
  vehicle_in_zone: "Техника в запрещённой зоне",
  vehicle_collision: "Встречный курс двух единиц техники",
};

export type ZoneKind = "machinery" | "fence" | "height" | "other" | "danger"
  | "flammable"
  | "under_load"
  | "two_person"
  | "crowd_limit";
export type Point = [number, number];

/** Когда зона действует, ЧЧ:ММ местного времени; start > end — через полночь. */
export interface ZoneSchedule {
  start: string;
  end: string;
}

export interface Zone {
  id: string;
  kind: ZoneKind;
  name?: string;
  polygon: Point[];
  schedule?: ZoneSchedule;
}

export const ZONE_LABELS = Object.fromEntries(
  ZONE_KIND_LIST.map((k) => [k, ZONE_KINDS[k].label]),
) as Record<ZoneKind, string>;

export type MetricsDevice = "linq" | "cpu" | "gpu";

export interface Metrics {
  camera_id: string;
  device: MetricsDevice;
  fps: number;
  inference_ms: number;
  e2e_ms?: number;
  ts: string;
}
