import { describe, expect, it } from "vitest";
import { MIN_EVENTS, buildHeatGrid, convexHull, suggestZones } from "./heatmap";
import type { ViolationEvent } from "./types";

const at = (x: number, y: number, w = 0.04): ViolationEvent =>
  ({ bbox: [x - w, y - 0.2, x + w, y] }) as ViolationEvent;
const cluster = (x: number, y: number, n: number) => Array.from({ length: n }, () => at(x, y));

describe("buildHeatGrid", () => {
  it("пусто — максимум 0", () => {
    const g = buildHeatGrid([]);
    expect(g.count).toBe(0);
    expect(g.max).toBe(0);
  });
  it("считает по точке стоп-линии и игнорирует битый bbox", () => {
    const g = buildHeatGrid([at(0.5, 0.5), { bbox: [NaN, 0, 1, 1] } as ViolationEvent]);
    expect(g.count).toBe(1);
    const peak = g.cells.indexOf(g.max);
    expect(Math.floor(peak % g.cols)).toBe(Math.floor(0.5 * g.cols));
  });
});

describe("suggestZones", () => {
  it("мало событий — пусто", () => {
    expect(suggestZones(buildHeatGrid(cluster(0.3, 0.5, MIN_EVENTS - 1)))).toEqual([]);
  });
  it("два пятна — две зоны, большее первым, внутри 0..1", () => {
    const grid = buildHeatGrid([...cluster(0.25, 0.5, 12), ...cluster(0.75, 0.8, 6)]);
    const zones = suggestZones(grid, 0.2);
    expect(zones.length).toBe(2);
    expect(zones[0].share).toBeGreaterThan(zones[1].share);
    for (const z of zones) {
      expect(z.polygon.length).toBeGreaterThanOrEqual(3);
      expect(z.polygon.flat().every((v) => v >= 0 && v <= 1)).toBe(true);
    }
    const xs = zones[0].polygon.map(([x]) => x);
    expect(Math.min(...xs)).toBeLessThan(0.25);
    expect(Math.max(...xs)).toBeGreaterThan(0.2);
  });
});

describe("convexHull", () => {
  it("отбрасывает внутреннюю точку", () => {
    const hull = convexHull([[0, 0], [1, 0], [1, 1], [0, 1], [0.5, 0.5]]);
    expect(hull).toHaveLength(4);
  });
});
