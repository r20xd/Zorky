import { describe, expect, it } from "vitest";
import { canControlLinq, linqHeadline, measureRows, measureTotals, parseLinq } from "./linq";
import type { Metrics } from "./types";

const base = {
  state: "connected",
  busy: false,
  host: "10.0.114.30",
  device: "linq",
  devices: ["cpu", "gpu", "linq"],
  ready: true,
  log: ["ok"],
};

describe("parseLinq", () => {
  it("разбирает полный ответ", () => {
    const s = parseLinq({
      ...base,
      report: { programs: ["a.tpu"], broker: true, modules: { pika: true, cv2: false }, checked_at: "t" },
    });
    expect(s?.ready).toBe(true);
    expect(s?.report?.programs).toEqual(["a.tpu"]);
    expect(s?.report?.modules).toEqual({ pika: true, cv2: false });
  });
  it("неполный или чужой — null", () => {
    expect(parseLinq(null)).toBeNull();
    expect(parseLinq({ ...base, state: "?" })).toBeNull();
    expect(parseLinq({ ...base, device: "tpu9" })).toBeNull();
  });
  it("без отчёта и с мусором в списках", () => {
    const s = parseLinq({ ...base, report: null, log: ["a", 1], devices: ["cpu", "x"] });
    expect(s?.report).toBeNull();
    expect(s?.log).toEqual(["a"]);
    expect(s?.devices).toEqual(["cpu"]);
  });
});

describe("linqHeadline", () => {
  const s = (over: object) => parseLinq({ ...base, ...over })!;
  it("состояния", () => {
    expect(linqHeadline(s({ busy: true })).tone).toBe("warn");
    expect(linqHeadline(s({ state: "disconnected", ready: false })).tone).toBe("bad");
    expect(linqHeadline(s({ ready: false })).tone).toBe("warn");
    expect(linqHeadline(s({})).tone).toBe("ok");
  });
});

describe("замеры", () => {
  const now = Date.parse("2026-10-24T10:00:10Z");
  const m = (camera_id: string, device: Metrics["device"], fps: number, ts = "2026-10-24T10:00:05Z"): Metrics => ({
    camera_id,
    device,
    fps,
    inference_ms: 20,
    ts,
  });
  it("строки по камерам, устаревшие помечены", () => {
    const rows = measureRows([m("b", "cpu", 5), m("a", "linq", 12, "2026-10-24T09:00:00Z")], now);
    expect(rows.map((r) => r.camera)).toEqual(["a", "b"]);
    expect(rows[0]).toMatchObject({ device: "LinQ", stale: true, onLinq: true });
    expect(rows[1].stale).toBe(false);
  });
  it("итоги по свежим", () => {
    expect(measureTotals([], now)).toBeNull();
    const t = measureTotals(
      [m("a", "linq", 10), m("b", "linq", 14), m("c", "cpu", 1, "2026-10-24T08:00:00Z")],
      now,
    );
    expect(t).toEqual({ cameras: 2, fps: 24, inferenceMs: 20 });
  });
});

describe("права", () => {
  it("управляет только admin", () => {
    expect(canControlLinq("admin")).toBe(true);
    expect(canControlLinq("manager")).toBe(false);
    expect(canControlLinq(null)).toBe(true);
  });
});

import { deviceSummaries, parseProgram, pipelineNodes } from "./linq";

describe("parseProgram", () => {
  it("разбирает имя компилятора", () => {
    expect(parseProgram("ppe16_b1_o5_h1m.tpu")).toEqual({
      file: "ppe16_b1_o5_h1m.tpu",
      model: "ppe16",
      batch: "1",
      preset: "O5",
      platform: "H1M",
      ours: true,
    });
  });
  it("образец организаторов не считается нашим", () => {
    expect(parseProgram("torch_resnet50_b1_o5_h1m.tpu").ours).toBe(false);
  });
  it("чужое имя не ломает разбор", () => {
    expect(parseProgram("x.tpu")).toMatchObject({ model: "x", preset: "", ours: false });
  });
});

describe("устройства и конвейер", () => {
  const now = Date.parse("2026-10-24T10:00:10Z");
  const fresh = "2026-10-24T10:00:05Z";
  const m = (camera_id: string, device: Metrics["device"], fps: number, inference_ms: number, ts = fresh): Metrics => ({
    camera_id,
    device,
    fps,
    inference_ms,
    ts,
  });
  const status = (over: object) => parseLinq({ ...base, report: { programs: ["ppe16_b1_o5_h1m.tpu"], broker: true, modules: {} }, ...over })!;

  it("сводка по устройствам без устаревших", () => {
    const list = deviceSummaries(
      [m("a", "cpu", 1, 1000), m("b", "cpu", 2, 500), m("c", "linq", 10, 40), m("d", "gpu", 9, 9, "2026-10-24T09:00:00Z")],
      now,
    );
    expect(list.map((x) => x.device)).toEqual(["linq", "cpu"]);
    expect(list[1]).toMatchObject({ cameras: 2, fps: 3, inferenceMs: 750 });
  });
  it("пусто — пустой список", () => {
    expect(deviceSummaries([], now)).toEqual([]);
  });
  it("ускоритель зелёный, когда камеры идут на LinQ", () => {
    const tpu = pipelineNodes(status({}), [m("a", "linq", 5, 100)], now).find((n) => n.id === "tpu")!;
    expect(tpu.tone).toBe("ok");
  });
  it("стенд подключён, программа есть, но камеры на CPU — предупреждение", () => {
    const nodes = pipelineNodes(status({}), [m("a", "cpu", 1, 900)], now);
    expect(nodes.find((n) => n.id === "tpu")!.tone).toBe("warn");
    expect(nodes.find((n) => n.id === "broker")!.note).toBe("доступен");
  });
  it("без стенда и без замеров ничего не выдумывает", () => {
    const nodes = pipelineNodes(status({ state: "disconnected", ready: false, report: null }), [], now);
    expect(nodes.find((n) => n.id === "tpu")!.tone).toBe("idle");
    expect(nodes.find((n) => n.id === "broker")!.tone).toBe("idle");
    expect(nodes.find((n) => n.id === "camera")!.tone).toBe("bad");
  });
});

describe("воркер в пути кадра", () => {
  it("называет устройство по замерам, а не по настройке", () => {
    const s = parseLinq({ ...base, device: "linq" })!;
    const metric = { camera_id: "a", device: "cpu", fps: 1, inference_ms: 9, ts: "2026-10-24T10:00:05Z" } as Metrics;
    const node = pipelineNodes(s, [metric], Date.parse("2026-10-24T10:00:10Z")).find((n) => n.id === "worker")!;
    expect(node.note).toBe("считает на CPU");
  });
});

import { parseBench } from "./linq";

describe("parseBench", () => {
  const raw = {
    program: "ppe16",
    calibration_frames: 16,
    latency_ms: { p50: 166.3, p95: 289.3, mean: 185.4 },
    fps_by_inflight: { "4": 12, "1": 5.7, "x": 3 },
    best: { conf: 0.4, f1: 0.57, recall: 0.71, precision: 0.47 },
    loaded: { latency_ms: { p50: 949 }, fps: 1 },
  };
  it("разбирает полный ответ, FPS по возрастанию числа запросов", () => {
    const b = parseBench(raw)!;
    expect(b.program).toBe("ppe16");
    expect(b.calibrationFrames).toBe(16);
    expect(b.fps).toEqual([
      { inflight: 1, fps: 5.7 },
      { inflight: 4, fps: 12 },
    ]);
    expect(b.best?.f1).toBe(0.57);
    expect(b.loaded).toEqual({ p50: 949, fps: 1 });
  });
  it("нет замеров или мусор — null или пустые части", () => {
    expect(parseBench(null)).toBeNull();
    expect(parseBench({ latency_ms: {} })).toBeNull();
    const b = parseBench({ program: "p", latency_ms: { p50: "x" }, best: { conf: 1 } })!;
    expect(b.latency).toBeNull();
    expect(b.best).toBeNull();
    expect(b.fps).toEqual([]);
  });
  it("parseLinq кладёт bench в статус", () => {
    const s = parseLinq({ ...base, report: null, bench: raw })!;
    expect(s.bench?.program).toBe("ppe16");
    expect(parseLinq({ ...base, report: null })!.bench).toBeNull();
  });
});
