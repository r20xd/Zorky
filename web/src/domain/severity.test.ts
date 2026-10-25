import { describe, expect, it } from "vitest";
import { countBySeverity, severityOf, startedAt, waitClock, waitTone } from "./events";
import type { Severity, ViolationEvent, ViolationType } from "./types";

function event(type: ViolationType, severity: Severity, status: ViolationEvent["status"] = "new"): ViolationEvent {
  return {
    id: `${type}-${severity}-${status}`,
    camera_id: "cam-1",
    ts: "2026-10-21T10:00:00Z",
    frame_id: 1,
    type,
    bbox: [0.1, 0.2, 0.3, 0.9],
    confidence: 0.9,
    track_id: 1,
    severity,
    incident_id: `inc-${type}-${severity}-${status}`,
    count: 1,
    first_seen: "2026-10-21T10:00:00Z",
    last_seen: "2026-10-21T10:00:00Z",
    status,
    status_history: [],
    received_at: "2026-10-21T10:00:01Z",
  };
}

describe("severityOf", () => {
  it("падение и неподвижность всегда критичны", () => {
    expect(severityOf(event("fall", "medium"))).toBe("critical");
    expect(severityOf(event("immobile", "low"))).toBe("critical");
    expect(severityOf(event("no_helmet", "medium"))).toBe("medium");
  });

  it("без severity от бэкенда берёт критичность по типу", () => {
    const bare = (type: ViolationType) => ({ ...event(type, "low"), severity: undefined }) as unknown as ViolationEvent;
    expect(severityOf(bare("no_respirator"))).toBe("medium");
    expect(severityOf(bare("danger_zone"))).toBe("high");
    expect(severityOf(bare("running"))).toBe("low");
    expect(severityOf(bare("vehicle_proximity"))).toBe("critical");
  });
});

describe("startedAt", () => {
  it("без first_seen берёт время события", () => {
    const e = event("fall", "critical");
    expect(startedAt(e)).toBe(e.first_seen);
    expect(startedAt({ ...e, first_seen: undefined } as unknown as ViolationEvent)).toBe(e.ts);
  });
});

describe("countBySeverity", () => {
  it("считает активные события по критичности, ложные пропускает", () => {
    const list = [event("fall", "critical"), event("smoking", "low"), event("no_helmet", "medium", "false")];
    expect(countBySeverity(list)).toEqual({ critical: 1, high: 0, medium: 0, low: 1 });
  });
});

describe("waitTone", () => {
  it("растёт с ожиданием: 2, 5 и 15 минут — границы", () => {
    expect(waitTone(119)).toBe("ok");
    expect(waitTone(120)).toBe("warn");
    expect(waitTone(299)).toBe("warn");
    expect(waitTone(300)).toBe("high");
    expect(waitTone(899)).toBe("high");
    expect(waitTone(900)).toBe("crit");
  });
});

describe("waitClock", () => {
  const since = "2026-10-21T10:00:00Z";
  it("показывает ММ:СС", () => {
    expect(waitClock(since, Date.parse(since) + 71_000)).toBe("01:11");
    expect(waitClock(since, Date.parse(since) + 47 * 60_000 + 18_000)).toBe("47:18");
  });
  it("не уходит в минус и не ломается на мусоре", () => {
    expect(waitClock(since, Date.parse(since) - 5000)).toBe("00:00");
    expect(waitClock("не время", 0)).toBe("");
  });
});
