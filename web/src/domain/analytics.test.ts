import { describe, expect, it } from "vitest";
import { NO_FILTER, computeAnalytics, filterEvents, peakHour, typeShares, zonesOf } from "./analytics";
import type { ViolationEvent } from "./types";

function ev(over: Partial<ViolationEvent>): ViolationEvent {
  return {
    id: "1",
    camera_id: "cam-1",
    ts: new Date(2026, 9, 21, 10, 5).toISOString(),
    frame_id: 1,
    type: "no_helmet",
    bbox: [0, 0, 1, 1],
    confidence: 0.9,
    track_id: 1,
    severity: "high",
    incident_id: "inc",
    count: 1,
    first_seen: "2026-10-21T10:00:00Z",
    last_seen: "2026-10-21T10:00:00Z",
    status: "new",
    status_history: [],
    received_at: "2026-10-21T10:00:00Z",
    ...over,
  };
}

describe("computeAnalytics", () => {
  it("пусто", () => {
    const a = computeAnalytics([]);
    expect(a.total).toBe(0);
    expect(a.byHour).toHaveLength(24);
    expect(a.byType.smoking).toBe(0);
    expect(a.byCamera).toEqual({});
  });

  it("считает по типам, камерам и часам", () => {
    const a = computeAnalytics([
      ev({ id: "1" }),
      ev({ id: "2", type: "smoking", camera_id: "cam-2" }),
      ev({ id: "3", ts: new Date(2026, 9, 21, 23, 59).toISOString() }),
    ]);
    expect(a.total).toBe(3);
    expect(a.byType.no_helmet).toBe(2);
    expect(a.byType.smoking).toBe(1);
    expect(a.byCamera).toEqual({ "cam-1": 2, "cam-2": 1 });
    expect(a.byHour[10]).toBe(2);
    expect(a.byHour[23]).toBe(1);
  });

  it("битый ts не попадает в часы", () => {
    const a = computeAnalytics([ev({ ts: "oops" })]);
    expect(a.total).toBe(1);
    expect(a.byHour.reduce((s, n) => s + n, 0)).toBe(0);
  });

  it("зоны, дни, смены, доля ложных, время реакции", () => {
    const at = (h: number) => new Date(2026, 9, 21, h, 0).toISOString();
    const a = computeAnalytics([
      ev({
        id: "1",
        ts: at(3),
        zone_id: "z1",
        status: "resolved",
        status_history: [
          { status: "sent_to_master", ts: "2026-10-21T10:01:00Z", by: "op" },
          { status: "resolved", ts: "2026-10-21T10:11:00Z", by: "m" },
        ],
      }),
      ev({ id: "2", ts: at(12), status: "false" }),
      ev({ id: "3", ts: new Date(2026, 9, 22, 20, 0).toISOString() }),
      ev({ id: "4", ts: at(9) }),
    ]);
    expect(a.byZone).toEqual({ z1: 1, "без зоны": 3 });
    expect(a.byShift).toEqual({ night: 1, day: 2, evening: 1 });
    expect(Object.keys(a.byDay)).toEqual(["2026-10-21", "2026-10-22"]);
    expect(a.byDay["2026-10-21"]).toBe(3);
    expect(a.falseShare).toBe(0.25);
    expect(a.avgToMasterMs).toBe(60_000);
    expect(a.avgToResolvedMs).toBe(600_000);
    expect(computeAnalytics([]).avgToMasterMs).toBeNull();
  });
});

describe("фильтры и доли", () => {
  const now = new Date(2026, 9, 21, 12, 0).getTime();
  const events = [
    ev({ id: "a", camera_id: "cam-1", zone_id: "z1", type: "no_helmet", ts: new Date(2026, 9, 21, 9, 0).toISOString() }),
    ev({ id: "b", camera_id: "cam-2", type: "smoking", ts: new Date(2026, 9, 21, 18, 0).toISOString() }),
    ev({ id: "c", camera_id: "cam-1", type: "no_helmet", ts: new Date(2026, 9, 10, 18, 0).toISOString() }),
    ev({ id: "d", ts: "битая дата" }),
  ];

  it("без фильтра отбрасывает только битый ts", () => {
    expect(filterEvents(events, NO_FILTER, now).map((e) => e.id)).toEqual(["a", "b", "c"]);
  });

  it("камера, зона, период и смена", () => {
    expect(filterEvents(events, { ...NO_FILTER, camera: "cam-1" }, now).map((e) => e.id)).toEqual(["a", "c"]);
    expect(filterEvents(events, { ...NO_FILTER, zone: "z1" }, now).map((e) => e.id)).toEqual(["a"]);
    expect(filterEvents(events, { ...NO_FILTER, zone: "без зоны" }, now).map((e) => e.id)).toEqual(["b", "c"]);
    expect(filterEvents(events, { ...NO_FILTER, period: "7d" }, now).map((e) => e.id)).toEqual(["a", "b"]);
    expect(filterEvents(events, { ...NO_FILTER, shift: "evening" }, now).map((e) => e.id)).toEqual(["b", "c"]);
  });

  it("доли по типам: сумма 1, нулевые в конце", () => {
    const shares = typeShares(filterEvents(events, NO_FILTER, now));
    expect(shares[0]).toMatchObject({ type: "no_helmet", count: 2 });
    expect(shares[0].share).toBeCloseTo(2 / 3);
    expect(shares.at(-1)?.count).toBe(0);
    expect(typeShares([]).every((s) => s.share === 0)).toBe(true);
  });

  it("пик по часам и список зон", () => {
    expect(peakHour(computeAnalytics(events.slice(0, 3)).byHour)).toBe(18);
    expect(peakHour(new Array(24).fill(0))).toBeNull();
    expect(zonesOf(events)).toEqual(["z1", "без зоны"]);
  });
});
