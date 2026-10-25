import { afterEach, describe, expect, it, vi } from "vitest";
import { countByType, mergeEvent } from "../domain/events";
import type { Zone, ViolationEvent, ViolationType } from "../domain/types";
import { listEvents, putZones, setEventStatus, subscribeEvents } from "./api";

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

describe("api", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("listEvents и setEventStatus ходят на относительные пути контракта", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ ...event("a"), status: "confirmed" }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    await listEvents(5);
    const updated = await setEventStatus("a/1", "confirmed");
    expect(updated.status).toBe("confirmed");
    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    expect(calls[0][0]).toBe("/api/events?limit=5");
    expect(calls[1][0]).toBe("/api/events/a%2F1");
    expect(calls[1][1].method).toBe("PATCH");
    expect(calls[1][1].body).toBe('{"status":"confirmed"}');
  });

  it("ошибка бэкенда становится Error с текстом", async () => {
    vi.stubGlobal("fetch", async () => ({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: async () => ({ error: "нет такого события" }),
    }));
    await expect(setEventStatus("x", "new")).rejects.toThrow("нет такого события");
  });

  it("SSE: события violation попадают в ленту, статус связи сообщается", () => {
    const listeners: Record<string, (m: { data: string }) => void> = {};
    const source = {
      addEventListener: (name: string, fn: (m: { data: string }) => void) => {
        listeners[name] = fn;
      },
      close: vi.fn(),
      onopen: null as null | (() => void),
      onerror: null as null | (() => void),
    };
    vi.stubGlobal("EventSource", function () {
      return source;
    });
    let list: ViolationEvent[] = [];
    const states: boolean[] = [];
    const stop = subscribeEvents(
      (e) => (list = mergeEvent(list, e)),
      (online) => states.push(online),
    );
    source.onopen?.();
    listeners.violation({ data: JSON.stringify(event("s1", "smoking")) });
    source.onerror?.();
    expect(list.map((e) => e.id)).toEqual(["s1"]);
    expect(countByType(list).smoking).toBe(1);
    expect(states).toEqual([true, false]);
    stop();
    expect(source.close).toHaveBeenCalled();
  });

  it("SSE: master, update и escalation доходят до ленты с именем события", () => {
    const listeners: Record<string, (m: { data: string }) => void> = {};
    vi.stubGlobal("EventSource", function () {
      return {
        addEventListener: (name: string, fn: (m: { data: string }) => void) => {
          listeners[name] = fn;
        },
        close: vi.fn(),
      };
    });
    const got: [string, string, number][] = [];
    subscribeEvents(
      (e, name) => got.push([name, e.status, e.count]),
      () => undefined,
    );
    const body = (over: Partial<ViolationEvent>) => ({ data: JSON.stringify({ ...event("a"), ...over }) });
    listeners.master(body({ status: "sent_to_master" }));
    listeners.update(body({ count: 4 }));
    listeners.escalation(body({ status: "sent_to_master" }));
    expect(got).toEqual([
      ["master", "sent_to_master", 1],
      ["update", "new", 4],
      ["escalation", "sent_to_master", 1],
    ]);
  });

  it("putZones шлёт PUT с массивом зон на /api/cameras/{id}/zones", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => [] }));
    vi.stubGlobal("fetch", fetchMock);
    const zones: Zone[] = [
      { id: "z-1", kind: "danger", polygon: [[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]] },
    ];
    await putZones("cam 1", zones);
    const [url, init] = (fetchMock.mock.calls as unknown as [string, RequestInit][])[0];
    expect(url).toBe("/api/cameras/cam%201/zones");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual(zones);
  });
});
