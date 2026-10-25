import { describe, expect, it } from "vitest";
import {
  areaRules,
  effectiveRules,
  missingZones,
  nextCameraId,
  rulesSummary,
  toggleRule,
  validateAreaName,
} from "./areaRules";
import { DEFAULT_RULES, RULE_INFO, RULE_TYPES } from "./ruleCatalog";
import type { Area, Camera, Zone } from "./types";
import { VIOLATION_LABELS } from "./types";

const cam = (extra: Partial<Camera> = {}): Camera => ({ id: "c", name: "c", ...extra });
const zone = (kind: Zone["kind"]): Zone => ({ id: kind, kind, polygon: [[0, 0], [1, 0], [1, 1]] });

describe("каталог правил", () => {
  it("описывает ровно те типы, что есть в реестре вебом", () => {
    expect([...RULE_TYPES].sort()).toEqual(Object.keys(VIOLATION_LABELS).sort());
  });
  it("критичные идут первыми, по умолчанию включено 8", () => {
    expect(RULE_INFO[RULE_TYPES[0]].severity).toBe("critical");
    expect(DEFAULT_RULES).toHaveLength(8);
    expect(DEFAULT_RULES).toContain("fall");
    expect(DEFAULT_RULES).not.toContain("running");
  });
});

describe("effectiveRules", () => {
  const area: Area = { name: "Цех", rules: ["fall", "smoking"], cameras: [] };
  it("свои правила камеры важнее участка, участка — важнее умолчаний", () => {
    expect(effectiveRules(cam({ rules: ["smoking"] }), area)).toEqual({ enabled: ["smoking"], source: "camera" });
    expect(effectiveRules(cam(), area)).toEqual({ enabled: ["fall", "smoking"], source: "area" });
    expect(effectiveRules(cam(), { name: "Склад", cameras: [] })).toEqual({
      enabled: DEFAULT_RULES,
      source: "default",
    });
    expect(effectiveRules(cam())).toEqual({ enabled: DEFAULT_RULES, source: "default" });
  });
  it("пустой список у камеры — контролировать ничего (а не «по умолчанию»)", () => {
    expect(effectiveRules(cam({ rules: [] }), area)).toEqual({ enabled: [], source: "camera" });
  });
  it("неизвестные коды из бэкенда отбрасываются", () => {
    expect(areaRules({ name: "x", rules: ["fall", "from_the_future"], cameras: [] })).toEqual(["fall"]);
  });
});

describe("toggleRule", () => {
  it("включает и выключает, порядок — порядок показа", () => {
    const on = toggleRule(["smoking"], "fall");
    expect(on).toEqual(["fall", "smoking"]);
    expect(toggleRule(on, "fall")).toEqual(["smoking"]);
  });
});

describe("missingZones", () => {
  it("предупреждает о правилах, которым нужна зона, а её нет", () => {
    expect(missingZones(["danger_zone", "fall", "fence_crossing"], [zone("danger")])).toEqual([
      { type: "fence_crossing", kind: "fence" },
    ]);
    expect(missingZones(["fall", "smoking"], [])).toEqual([]);
  });
});

describe("новый участок", () => {
  it("nextCameraId берёт первый свободный", () => {
    expect(nextCameraId([])).toBe("cam-1");
    expect(nextCameraId([cam({ id: "cam-1" }), cam({ id: "cam-3" })])).toBe("cam-4");
    expect(nextCameraId([cam({ id: "cam-2" })])).toBe("cam-3");
  });
  it("название: не пустое, не длинное, без повторов", () => {
    const areas: Area[] = [{ name: "Цех 1", cameras: [] }];
    expect(validateAreaName("  ", areas)).toMatch(/укажите/);
    expect(validateAreaName("x".repeat(65), areas)).toMatch(/64/);
    expect(validateAreaName("цех 1", areas)).toMatch(/уже есть/);
    expect(validateAreaName("Склад", areas)).toBeNull();
  });
  it("сводка", () => {
    expect(rulesSummary(DEFAULT_RULES)).toBe("8 из 18");
  });
});
