import { describe, expect, it } from "vitest";
import { EMPTY_FILTERS, buildQuery, csvUrl, eventsUrl, formatDuration, reactionMs } from "./journal";
import type { ViolationEvent } from "./types";

describe("buildQuery", () => {
  it("пустые фильтры — пустая строка", () => {
    expect(buildQuery(EMPTY_FILTERS)).toBe("");
    expect(csvUrl(EMPTY_FILTERS)).toBe("/api/events.csv");
  });
  it("собирает только заполненные фильтры и страницу", () => {
    const query = buildQuery(
      { ...EMPTY_FILTERS, type: "fall", camera: "cam-1", zone: " z1 ", status: "new" },
      { limit: 50, offset: 100 },
    );
    expect(query).toBe("type=fall&camera=cam-1&zone=z1&status=new&limit=50&offset=100");
  });
  it("время переводит в ISO UTC", () => {
    const query = buildQuery({ ...EMPTY_FILTERS, from: "2026-10-21T10:00" });
    expect(query).toBe(`from=${encodeURIComponent(new Date("2026-10-21T10:00").toISOString())}`);
  });
  it("eventsUrl и csvUrl используют тот же запрос", () => {
    const f = { ...EMPTY_FILTERS, status: "false" as const };
    expect(eventsUrl(f, { limit: 10, offset: 0 })).toBe("/api/events?status=false&limit=10&offset=0");
    expect(csvUrl(f)).toBe("/api/events.csv?status=false");
  });
});

describe("reactionMs", () => {
  const base = {
    first_seen: "2026-10-21T10:00:00Z",
    status_history: [],
  } as unknown as ViolationEvent;
  it("null без реакции, иначе от first_seen до первой смены", () => {
    expect(reactionMs(base)).toBeNull();
    const reacted = {
      ...base,
      status_history: [
        { status: "new", ts: "2026-10-21T10:00:00Z", by: "system" },
        { status: "confirmed", ts: "2026-10-21T10:01:30Z", by: "op" },
      ],
    } as ViolationEvent;
    expect(reactionMs(reacted)).toBe(90_000);
  });
  it("автопередача мастеру системой — не реакция", () => {
    const auto = {
      ...base,
      status_history: [{ status: "sent_to_master", ts: "2026-10-21T10:00:01Z", by: "system" }],
    } as ViolationEvent;
    expect(reactionMs(auto)).toBeNull();
    const handled = {
      ...auto,
      status_history: [
        ...auto.status_history,
        { status: "resolved", ts: "2026-10-21T10:00:40Z", by: "master" },
      ],
    } as ViolationEvent;
    expect(reactionMs(handled)).toBe(40_000);
  });
  it("форматирует длительность", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(90_000)).toBe("1 мин 30 с");
  });
});
