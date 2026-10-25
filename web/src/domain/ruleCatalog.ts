import type { Severity, ViolationType, ZoneKind } from "./types";

export interface RuleInfo {
  /** включено у камеры, пока специалист не выбрал своё (раздел default в ml/worker/rules.yaml) */
  defaultOn: boolean;
  /** вид зоны, без которой правило ничего не найдёт */
  zoneKind?: ZoneKind;
  severity: Severity;
}

/** Что умеет каждое правило. Как в contracts/violation_types.json (сверяет tests/test_contract_types.py). */
export const RULE_INFO: Record<ViolationType, RuleInfo> = {
  fall: { defaultOn: true, severity: "critical" },
  immobile: { defaultOn: true, severity: "critical" },
  vehicle_proximity: { defaultOn: false, severity: "critical" },
  vehicle_collision: { defaultOn: false, severity: "critical" },
  no_helmet: { defaultOn: true, severity: "high" },
  danger_zone: { defaultOn: true, zoneKind: "danger", severity: "high" },
  smoking: { defaultOn: true, severity: "high" },
  phone: { defaultOn: false, zoneKind: "machinery", severity: "high" },
  fence_crossing: { defaultOn: true, zoneKind: "fence", severity: "high" },
  under_load: { defaultOn: false, zoneKind: "under_load", severity: "high" },
  distress_signal: { defaultOn: true, severity: "high" },
  lone_worker: { defaultOn: false, zoneKind: "two_person", severity: "high" },
  vehicle_in_zone: { defaultOn: false, zoneKind: "danger", severity: "high" },
  no_respirator: { defaultOn: true, severity: "medium" },
  overcrowding: { defaultOn: false, zoneKind: "crowd_limit", severity: "medium" },
  at_height: { defaultOn: false, zoneKind: "height", severity: "medium" },
  loitering: { defaultOn: false, zoneKind: "danger", severity: "medium" },
  running: { defaultOn: false, zoneKind: "machinery", severity: "low" },
};

/** Порядок показа: от критичных к низким, внутри — включённые по умолчанию выше. */
const SEVERITY_ORDER: Severity[] = ["critical", "high", "medium", "low"];
export const RULE_TYPES = (Object.keys(RULE_INFO) as ViolationType[]).sort(
  (a, b) =>
    SEVERITY_ORDER.indexOf(RULE_INFO[a].severity) - SEVERITY_ORDER.indexOf(RULE_INFO[b].severity) ||
    Number(RULE_INFO[b].defaultOn) - Number(RULE_INFO[a].defaultOn),
);

export const DEFAULT_RULES: ViolationType[] = RULE_TYPES.filter((type) => RULE_INFO[type].defaultOn);
