import { describe, expect, it } from "vitest";
import { bboxStyle, upsertEvent, countByType, liveBoxes, mergeEvent, replaceEvent, violatingCameras, sortFeed, statusBadge, repeatLabel, shouldBeep } from "./events";
import type { ViolationEvent, ViolationType } from "./types";

function event(id: string, type: ViolationType = "no_helmet"): ViolationEvent {
  return {
    id,
    camera_id: "cam-1",
    ts: "2026-10-21T10:00:00Z",
    frame_id: 1,
    type,
    bbox: [0.1, 0.2, 0.3, 0.9],
    confidence: 0.9,
    track_id: 1,
    severity: "high",
    incident_id: `inc-${id}`,
    count: 1,
    first_seen: "2026-10-21T10:00:00Z",
    last_seen: "2026-10-21T10:00:00Z",
    status: "new",
    status_history: [],
    received_at: "2026-10-21T10:00:01Z",
  };
}

describe("mergeEvent", () => {
  it("ставит новое событие сверху и не дублирует", () => {
    const list = mergeEvent(mergeEvent([], event("a")), event("b"));
    expect(list.map((item) => item.id)).toEqual(["b", "a"]);
    expect(mergeEvent(list, event("a")).map((item) => item.id)).toEqual(["a", "b"]);
  });

  it("держит не больше limit событий", () => {
    let list: ViolationEvent[] = [];
    for (let i = 0; i < 5; i++) list = mergeEvent(list, event(String(i)), 3);
    expect(list.map((item) => item.id)).toEqual(["4", "3", "2"]);
  });
});

describe("upsertEvent", () => {
  it("известное событие обновляет на месте, новое — ставит сверху", () => {
    const list = [event("a"), event("b")];
    const updated = upsertEvent(list, { ...event("b"), status: "sent_to_master", count: 3 });
    expect(updated.map((e) => e.id)).toEqual(["a", "b"]);
    expect(updated[1]).toMatchObject({ status: "sent_to_master", count: 3 });
    expect(upsertEvent(list, event("c")).map((e) => e.id)).toEqual(["c", "a", "b"]);
  });
});

describe("replaceEvent", () => {
  it("меняет событие с тем же id", () => {
    const updated: ViolationEvent = { ...event("a"), status: "false" };
    const list = replaceEvent([event("a"), event("b")], updated);
    expect(list.map((item) => item.status)).toEqual(["false", "new"]);
  });
});

describe("countByType", () => {
  it("считает события по типам", () => {
    const counts = countByType([event("a"), event("b", "smoking"), event("c")]);
    expect(counts.no_helmet).toBe(2);
    expect(counts.smoking).toBe(1);
    expect(counts.fall).toBe(0);
  });
});

describe("счётчики после разметки", () => {
  it("ложное срабатывание уходит из счётчиков, подтверждённое остаётся", () => {
    let list = [event("a"), event("b")];
    list = replaceEvent(list, { ...event("a"), status: "false" });
    list = replaceEvent(list, { ...event("b"), status: "confirmed" });
    expect(countByType(list).no_helmet).toBe(1);
  });
});

describe("рамки на камерах", () => {
  it("bboxStyle переводит 0..1 в проценты и обрезает по кадру", () => {
    expect(bboxStyle([0.1, 0.2, 0.3, 0.9])).toEqual({
      left: "10%",
      top: "20%",
      width: "20%",
      height: "70%",
    });
    expect(bboxStyle([-0.5, 0, 2, 1]).width).toBe("100%");
  });

  it("liveBoxes: только своя камера, свежие, не ложные", () => {
    const other = { ...event("o"), camera_id: "cam-2" };
    const fp = { ...event("f"), status: "false" as const };
    const list = [event("a"), event("old"), other, fp, event("unseen")];
    const seen = { a: 9000, old: 1000, o: 9500, f: 9500 };
    expect(liveBoxes(list, seen, "cam-1", 10000).map((e) => e.id)).toEqual(["a"]);
    expect([...violatingCameras(list, seen, 10000)].sort()).toEqual(["cam-1", "cam-2"]);
  });
});

describe("openViolationsLabel", () => {
  it("считает неразобранные нарушения камеры со склонением", async () => {
    const { openViolationsLabel } = await import("./events");
    const mk = (id: string, status: "new" | "sent_to_master" | "resolved" | "false") => ({ ...event(id), status });
    expect(openViolationsLabel([], "cam-1")).toBe("Нарушений нет");
    expect(openViolationsLabel([mk("a", "resolved"), mk("b", "false")], "cam-1")).toBe("Нарушений нет");
    expect(openViolationsLabel([mk("a", "new")], "cam-1")).toBe("Открыто 1 нарушение");
    expect(openViolationsLabel([mk("a", "new"), mk("b", "sent_to_master")], "cam-1")).toBe("Открыто 2 нарушения");
    const five = ["a", "b", "c", "d", "e"].map((id) => mk(id, "new"));
    expect(openViolationsLabel(five, "cam-1")).toBe("Открыто 5 нарушений");
    expect(openViolationsLabel(five, "cam-9")).toBe("Нарушений нет");
  });
});

describe("snapshotSrc", () => {
  it("возвращает url снимка, без снимка — null", async () => {
    const { snapshotSrc } = await import("./events");
    expect(snapshotSrc({ ...event("a"), snapshot_url: "/api/snapshots/a.jpg" })).toBe("/api/snapshots/a.jpg");
    expect(snapshotSrc(event("b"))).toBeNull();
    expect(snapshotSrc({ ...event("c"), snapshot_url: "  " })).toBeNull();
  });
});

describe("лента K7", () => {
  const at = (e: ViolationEvent, last: string): ViolationEvent => ({ ...e, last_seen: last });

  it("критичные сверху, внутри группы свежие выше", () => {
    const list = [
      at(event("a"), "2026-10-21T10:05:00Z"),
      at(event("f", "fall"), "2026-10-21T10:01:00Z"),
      at(event("b"), "2026-10-21T10:09:00Z"),
      at(event("i", "immobile"), "2026-10-21T10:03:00Z"),
    ];
    expect(sortFeed(list).map((e) => e.id)).toEqual(["i", "f", "b", "a"]);
  });

  it("бейджи статусов, включая «Передано мастеру»", () => {
    expect(statusBadge("sent_to_master").label).toBe("Передано мастеру");
    expect(statusBadge("new").className).toContain("status-new");
    expect(statusBadge("false").label).toBe("Ложное");
  });

  it("повтор инцидента обновляет карточку, а не дублирует", () => {
    const first = event("a");
    const again = { ...event("b"), incident_id: first.incident_id, count: 3 };
    const list = mergeEvent([first], again);
    expect(list).toHaveLength(1);
    expect(repeatLabel(list[0])).toBe("×3");
    expect(repeatLabel(first)).toBeNull();
  });

  it("звук: только критичные новые или с ростом счётчика", () => {
    const fall = event("f", "fall");
    expect(shouldBeep([], fall)).toBe(true);
    expect(shouldBeep([fall], { ...fall, id: "f2" })).toBe(false);
    expect(shouldBeep([fall], { ...fall, id: "f2", count: 2 })).toBe(true);
    expect(shouldBeep([], event("a"))).toBe(false);
  });
});
