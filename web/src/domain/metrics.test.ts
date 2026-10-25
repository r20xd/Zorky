import { describe, expect, it } from "vitest";
import { formatMs, isStale, linqStatus, parseMetrics } from "./metrics";

const good = {
  camera_id: "cam-1",
  device: "linq",
  fps: 12.5,
  inference_ms: 40,
  e2e_ms: 120,
  ts: "2026-10-21T10:00:00Z",
};

describe("parseMetrics", () => {
  it("разбирает корректные записи", () => {
    expect(parseMetrics([good])).toEqual([good]);
  });

  it("e2e_ms необязателен", () => {
    const { e2e_ms: _, ...rest } = good;
    expect(parseMetrics([rest])[0].e2e_ms).toBeUndefined();
  });

  it("отбрасывает мусор и не падает на не-массиве", () => {
    expect(parseMetrics([good, null, { ...good, device: "tpu" }, { ...good, fps: "1" }])).toHaveLength(1);
    expect(parseMetrics({ error: "x" })).toEqual([]);
    expect(parseMetrics(undefined)).toEqual([]);
  });
});

describe("isStale / formatMs", () => {
  it("устаревшие метрики", () => {
    const m = parseMetrics([good])[0];
    const t = Date.parse(good.ts);
    expect(isStale(m, t + 5000)).toBe(false);
    expect(isStale(m, t + 60000)).toBe(true);
  });

  it("формат мс", () => {
    expect(formatMs(undefined)).toBe("—");
    expect(formatMs(40.4)).toBe("40 мс");
  });
});

describe("linqStatus", () => {
  const now = Date.parse("2026-10-21T10:00:10Z");
  const m = (device: "linq" | "cpu" | "gpu", ts = "2026-10-21T10:00:05Z") => ({
    camera_id: "cam-1",
    device,
    fps: 10,
    inference_ms: 20,
    ts,
  });

  it("подключён, если есть свежая запись с linq", () => {
    expect(linqStatus([m("linq"), m("cpu")], now).state).toBe("connected");
  });
  it("не подключён, если воркеры работают на cpu или gpu", () => {
    const s = linqStatus([m("cpu")], now);
    expect(s.state).toBe("fallback");
    expect(s.detail).toContain("CPU");
  });
  it("устаревший linq не считается подключённым", () => {
    expect(linqStatus([m("linq", "2026-10-21T09:00:00Z")], now).state).toBe("offline");
  });
  it("нет метрик — offline, нет доступа — unknown", () => {
    expect(linqStatus([], now).state).toBe("offline");
    expect(linqStatus(null, now).state).toBe("unknown");
  });
});
