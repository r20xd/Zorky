import typesSrc from "./types.ts?raw";
import { describe, expect, it } from "vitest";
import { ZONE_KINDS } from "./zoneKinds";

describe("zoneKinds", () => {
  it("виды совпадают с ZoneKind в types.ts", () => {
    const body = /export type ZoneKind =([^;]*);/.exec(typesSrc)![1];
    const kinds = [...body.matchAll(/"(\w+)"/g)].map((m) => m[1]);
    expect(Object.keys(ZONE_KINDS).sort()).toEqual(kinds.sort());
  });

  it("новые виды: названия, свои цвета, подсказки", () => {
    expect(ZONE_KINDS.under_load.label).toBe("Под грузом");
    expect(ZONE_KINDS.two_person.label).toBe("Только вдвоём");
    expect(ZONE_KINDS.crowd_limit.label).toBe("Лимит людей");
    const colors = Object.values(ZONE_KINDS).map((k) => k.color);
    expect(new Set(colors).size).toBe(colors.length);
    for (const k of Object.values(ZONE_KINDS)) expect(k.hint).toBeTruthy();
    // подсказки для специалиста, а не для разработчика: названия правил, без файлов и кодов
    expect(ZONE_KINDS.two_person.hint).toContain("Одиночная работа");
    for (const k of Object.values(ZONE_KINDS)) expect(k.hint).not.toMatch(/rules\.yaml|zone_kind|_/);
  });
});
