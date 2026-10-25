import { describe, expect, it } from "vitest";
import { groupMaster, masterQueue, masterTitle, masterView, sortMaster, waitLabel } from "./master";
import type { ViolationEvent } from "./types";

function ev(id: string, over: Partial<ViolationEvent> = {}): ViolationEvent {
  return {
    id, camera_id: "c", ts: "2026-10-21T10:00:00Z", frame_id: 1, type: "no_helmet",
    bbox: [0, 0, 1, 1], confidence: 1, track_id: 1, severity: "high", incident_id: id,
    count: 1, first_seen: "2026-10-21T10:00:00Z", last_seen: "2026-10-21T10:00:00Z",
    status: "sent_to_master", status_history: [], received_at: "2026-10-21T10:00:00Z", ...over,
  };
}

describe("groupMaster", () => {
  it("падение и неподвижность одного человека — одна карточка, главное — критичное и давнее", () => {
    const list = [
      ev("imm", { type: "immobile", track_id: 7, first_seen: "2026-10-21T10:00:10Z" }),
      ev("fall", { type: "fall", track_id: 7, first_seen: "2026-10-21T10:00:00Z" }),
      ev("other", { type: "no_helmet", track_id: 8 }),
    ];
    const groups = groupMaster(list);
    expect(groups.map((g) => g.main.id)).toEqual(["fall", "other"]);
    expect(groups[0].events.map((e) => e.id)).toEqual(["fall", "imm"]);
  });
  it("тот же трек на другой камере и события без трека не склеиваются", () => {
    const list = [
      ev("a", { camera_id: "c1", track_id: 3 }),
      ev("b", { camera_id: "c2", track_id: 3 }),
      ev("c", { track_id: null as unknown as number }),
      ev("d", { track_id: null as unknown as number }),
    ];
    expect(groupMaster(list).length).toBe(4);
  });
});

describe("master", () => {
  it("оставляет только sent_to_master", () => {
    const list = [ev("a"), ev("b", { status: "new" }), ev("c", { status: "resolved" }), ev("d", { status: "false" })];
    expect(masterQueue(list).map((e) => e.id)).toEqual(["a"]);
  });
  it("критичные сверху, потом давно ждущие", () => {
    const list = [
      ev("old", { first_seen: "2026-10-21T09:00:00Z" }),
      ev("new", { first_seen: "2026-10-21T10:00:00Z" }),
      ev("fall", { type: "fall", first_seen: "2026-10-21T10:30:00Z" }),
    ];
    expect(sortMaster(list).map((e) => e.id)).toEqual(["fall", "old", "new"]);
    expect(masterView([...list, ev("x", { status: "new" })]).length).toBe(3);
  });
  it("сколько ждёт", () => {
    const t = Date.parse("2026-10-21T10:00:00Z");
    expect(waitLabel("2026-10-21T10:00:00Z", t + 45000)).toBe("45 с");
    expect(waitLabel("2026-10-21T10:00:00Z", t + 180000)).toBe("3 мин");
    expect(waitLabel("2026-10-21T10:00:00Z", t + 3900000)).toBe("1 ч 05 мин");
    expect(waitLabel("bad", t)).toBe("");
  });
  it("заголовок", () => {
    expect(masterTitle(0)).toBe("Мастер");
    expect(masterTitle(3)).toBe("(3) Мастер");
  });
});
