import { describe, expect, it } from "vitest";
import {
  falseStats, formatWait, keepSelection, moveSelection, parseKey, queueOf, reasonOf, selectionAfterRemoval, waitingMs,
} from "./queue";
import type { ViolationEvent } from "./types";

function ev(id: string, over: Partial<ViolationEvent> = {}): ViolationEvent {
  return {
    id, camera_id: "cam-1", ts: "2026-10-21T10:00:00Z", frame_id: 1, type: "no_helmet", bbox: [0, 0, 1, 1],
    confidence: 0.9, track_id: 1, severity: "high", incident_id: id, count: 1,
    first_seen: "2026-10-21T10:00:00Z", last_seen: "2026-10-21T10:00:00Z", status: "new",
    status_history: [], received_at: "2026-10-21T10:00:00Z", ...over,
  };
}

describe("parseKey", () => {
  it("клавиши очереди", () => {
    expect(parseKey({ key: "j" })).toEqual({ kind: "move", step: 1 });
    expect(parseKey({ key: "K" })).toEqual({ kind: "move", step: -1 });
    expect(parseKey({ key: "1" })).toEqual({ kind: "confirm" });
    expect(parseKey({ key: "2" })).toEqual({ kind: "false" });
    expect(parseKey({ key: "3" })).toEqual({ kind: "handoff" });
    expect(parseKey({ key: "?" })).toEqual({ kind: "help" });
    expect(parseKey({ key: "x" })).toBeNull();
  });
  it("не срабатывает в полях ввода и с модификаторами", () => {
    expect(parseKey({ key: "1", typing: true })).toBeNull();
    expect(parseKey({ key: "j", ctrlKey: true })).toBeNull();
  });
  it("в меню причин цифры выбирают причину", () => {
    expect(parseKey({ key: "2", reasonOpen: true })).toEqual({ kind: "reason", reason: "ppe_present" });
    expect(parseKey({ key: "5", reasonOpen: true })).toEqual({ kind: "reason", reason: "other" });
    expect(parseKey({ key: "6", reasonOpen: true })).toBeNull();
    expect(parseKey({ key: "Escape", reasonOpen: true })).toEqual({ kind: "close" });
  });
});

describe("очередь", () => {
  const list = [
    ev("a", { last_seen: "2026-10-21T10:00:05Z" }),
    ev("b", { status: "confirmed" }),
    ev("c", { type: "fall" }),
    ev("d", { last_seen: "2026-10-21T10:00:09Z" }),
  ];
  it("только new, критичные сверху", () => {
    expect(queueOf(list).map((e) => e.id)).toEqual(["c", "d", "a"]);
  });
  it("таймер ожидания", () => {
    expect(waitingMs(ev("x"), Date.parse("2026-10-21T10:01:05Z"))).toBe(65000);
    expect(formatWait(65000)).toBe("01:05");
    expect(formatWait(3725000)).toBe("1:02:05");
  });
  it("J/K без зацикливания", () => {
    const q = queueOf(list);
    expect(moveSelection(q, null, 1)).toBe("c");
    expect(moveSelection(q, "c", 1)).toBe("d");
    expect(moveSelection(q, "a", 1)).toBe("a");
    expect(moveSelection(q, "c", -1)).toBe("c");
    expect(moveSelection([], "c", 1)).toBeNull();
  });
  it("выбор после решения", () => {
    const q = queueOf(list);
    const rest = q.filter((e) => e.id !== "d");
    expect(selectionAfterRemoval(q, "d", rest)).toBe("a");
    expect(selectionAfterRemoval(q, "a", q.slice(0, 2))).toBe("d");
    expect(selectionAfterRemoval(q.slice(0, 1), "c", [])).toBeNull();
    expect(keepSelection(q, "d")).toBe("d");
    expect(keepSelection(q, "zzz")).toBe("c");
  });
});

describe("falseStats", () => {
  it("по причинам и камерам", () => {
    const f = (id: string, cam: string, reason?: "duplicate") =>
      ev(id, {
        camera_id: cam, status: "false",
        status_history: [{ status: "false", ts: "2026-10-21T10:00:01Z", by: "op", ...(reason ? { reason } : {}) }],
      });
    const events = [f("1", "cam-1", "duplicate"), f("2", "cam-1", "duplicate"), f("3", "cam-2"), ev("4", { camera_id: "cam-2" })];
    const s = falseStats(events);
    expect(s.byReason).toEqual({ duplicate: 2, none: 1 });
    expect(s.byCamera["cam-1"]).toEqual({ falses: 2, total: 2, share: 1 });
    expect(s.byCamera["cam-2"].share).toBe(0.5);
    expect(reasonOf(events[0])).toBe("duplicate");
    expect(reasonOf(events[3])).toBeNull();
  });
});
