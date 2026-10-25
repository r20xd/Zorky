import { describe, expect, it } from "vitest";
import {
  ZONE_COLORS,
  centroid,
  defaultZoneName,
  finishZone,
  isNearFirst,
  nextZoneId,
  pointsAttr,
  removeZone,
  scheduleLabel,
  setZoneKind,
  setZoneSchedule,
  toNormalized,
  validateSchedule,
  zoneFill,
  zonesChanged,
} from "./zones";
import type { Zone } from "./types";

const rect = { left: 100, top: 50, width: 400, height: 200 };
const tri: [number, number][] = [[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]];

describe("zones", () => {
  it("переводит клик в 0..1 и зажимает за границы", () => {
    expect(toNormalized(300, 150, rect)).toEqual([0.5, 0.5]);
    expect(toNormalized(0, 999, rect)).toEqual([0, 1]);
    expect(toNormalized(1, 1, { ...rect, width: 0 })).toEqual([0, 0]);
  });

  it("закрывает зону только от 3 точек", () => {
    expect(finishZone([], tri.slice(0, 2), "danger")).toEqual([]);
    const zones = finishZone([], tri, "flammable", " Склад ");
    expect(zones).toEqual([{ id: "z-1", kind: "flammable", name: "Склад", polygon: tri }]);
  });

  it("id не повторяются, удаление работает", () => {
    const zones: Zone[] = [{ id: "z-2", kind: "danger", polygon: tri }];
    expect(nextZoneId(zones)).toBe("z-3");
    expect(removeZone(zones, "z-2")).toEqual([]);
  });

  it("тип зоны меняется и у каждого типа свой цвет", () => {
    const zones: Zone[] = [{ id: "z-1", kind: "other", polygon: tri }];
    expect(setZoneKind(zones, "z-1", "fence")[0].kind).toBe("fence");
    expect(new Set(Object.values(ZONE_COLORS)).size).toBe(9);
    expect(zoneFill("fence")).toBe(ZONE_COLORS.fence + "4d");
    expect(finishZone([], tri, "height")[0].kind).toBe("height");
  });

  it("строка точек для SVG", () => {
    expect(pointsAttr(tri)).toBe("0.1,0.1 0.9,0.1 0.5,0.9");
  });
});

describe("расписание зоны", () => {
  const zones: Zone[] = [{ id: "z-1", kind: "danger", polygon: tri }];
  it("проверяет формат и совпадение времени", () => {
    expect(validateSchedule({ start: "07:55", end: "12:00" })).toBeNull();
    expect(validateSchedule({ start: "7:55", end: "12:00" })).toMatch(/ЧЧ:ММ/);
    expect(validateSchedule({ start: "25:00", end: "12:00" })).toMatch(/ЧЧ:ММ/);
    expect(validateSchedule({ start: "10:00", end: "10:00" })).toMatch(/совпадают/);
    expect(validateSchedule({ start: "22:00", end: "06:00" })).toBeNull(); // через полночь
  });
  it("задаётся при создании и снимается", () => {
    const made = finishZone([], tri, "danger", "У стеллажей", { start: "07:55", end: "12:00" });
    expect(made[0].schedule).toEqual({ start: "07:55", end: "12:00" });
    expect(scheduleLabel(made[0].schedule)).toBe("07:55–12:00");
    expect(scheduleLabel(undefined)).toBe("всегда");
    const timed = setZoneSchedule(zones, "z-1", { start: "08:00", end: "16:00" });
    expect(timed[0].schedule?.end).toBe("16:00");
    expect(setZoneSchedule(timed, "z-1", undefined)[0]).not.toHaveProperty("schedule");
  });
});

describe("удобство разметки", () => {
  const tri3: [number, number][] = [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5]];
  const size = { width: 1000, height: 500 };
  it("клик у первой точки замыкает зону только когда точек хватает", () => {
    expect(isNearFirst(tri3, [0.105, 0.1], size)).toBe(true); // ~5 px
    expect(isNearFirst(tri3, [0.2, 0.1], size)).toBe(false); // 100 px
    expect(isNearFirst(tri3.slice(0, 2), [0.1, 0.1], size)).toBe(false); // точек меньше трёх
  });
  it("расстояние считается в пикселях, а не в долях кадра", () => {
    // по вертикали кадр вдвое ниже: 0.02 по y — это 10 px, по x 0.02 — 20 px
    expect(isNearFirst(tri3, [0.1, 0.12], size)).toBe(true);
    expect(isNearFirst(tri3, [0.12, 0.1], size)).toBe(false);
  });
  it("подпись в центре, имя по умолчанию по виду и числу зон", () => {
    expect(centroid([[0, 0], [1, 0], [1, 1], [0, 1]])).toEqual([0.5, 0.5]);
    const zones: Zone[] = [{ id: "z-1", kind: "danger", polygon: tri3 }];
    expect(defaultZoneName(zones, "danger")).toBe("Опасная зона 2");
    expect(defaultZoneName(zones, "fence")).toBe("Ограждение 1");
  });
  it("различает изменённые и сохранённые зоны", () => {
    const zones: Zone[] = [{ id: "z-1", kind: "danger", polygon: tri3 }];
    expect(zonesChanged(zones, [...zones])).toBe(false);
    expect(zonesChanged(zones, [])).toBe(true);
    expect(zonesChanged(setZoneKind(zones, "z-1", "fence"), zones)).toBe(true);
  });
});
