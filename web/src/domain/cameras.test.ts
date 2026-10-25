import { describe, expect, it } from "vitest";
import { cameraState, maskSource, validateCamera } from "./cameras";

describe("maskSource", () => {
  it("скрывает логин, пароль и путь", () => {
    expect(maskSource("rtsp://admin:secret@10.0.0.5:554/stream/1?token=x")).toBe("rtsp://***@10.0.0.5:554/…");
  });
  it("без логина и пути", () => {
    expect(maskSource("rtsp://cam.local")).toBe("rtsp://cam.local");
    expect(maskSource("rtsp://cam.local/live")).toBe("rtsp://cam.local/…");
  });
  it("пусто и не URL", () => {
    expect(maskSource(undefined)).toBe("—");
    expect(maskSource("demo/video.mp4")).toBe("demo/video.mp4");
  });
  it("пароль не просачивается", () => {
    expect(maskSource("rtsp://u:p%40ss@h/x")).not.toContain("p%40ss");
  });
});

describe("cameraState", () => {
  const m = { camera_id: "c", device: "cpu" as const, fps: 9.5, inference_ms: 40, e2e_ms: 120, ts: "2026-01-01T00:00:00Z" };
  const t = Date.parse(m.ts);
  it("в сети: кадр не старше 10 с", () => {
    expect(cameraState(m, t + 10000)).toEqual({ online: true, fps: 9.5, delayMs: 120 });
    expect(cameraState(m, t + 10001).online).toBe(false);
  });
  it("нет метрик — не в сети", () => {
    expect(cameraState(undefined, t)).toEqual({ online: false, fps: null, delayMs: null });
  });
});

describe("validateCamera", () => {
  it("проверяет поля", () => {
    expect(validateCamera({ id: "cam-1", name: "Цех", source: "rtsp://h/x" })).toBeNull();
    expect(validateCamera({ id: "ка м", name: "Цех", source: "rtsp://h" })).not.toBeNull();
    expect(validateCamera({ id: "c", name: " ", source: "rtsp://h" })).not.toBeNull();
    expect(validateCamera({ id: "c", name: "a" })).not.toBeNull();
  });
});
