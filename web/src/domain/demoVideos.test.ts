import { describe, expect, it } from "vitest";
import { fileProblem, nextVideoId, parseDemoState, sizeLabel, validCameraId } from "./demoVideos";

describe("parseDemoState", () => {
  it("разбирает ответ и отбрасывает мусор", () => {
    const s = parseDemoState({
      enabled: true,
      folder: "C:/v",
      videos: [{ name: "a.mp4", mb: 12.5 }, { mb: 1 }, null],
      players: [{ camera_id: "video-1", video: "a.mp4" }, { camera_id: 1 }],
    });
    expect(s).toEqual({
      enabled: true,
      folder: "C:/v",
      videos: [{ name: "a.mp4", mb: 12.5 }],
      players: [{ camera_id: "video-1", video: "a.mp4" }],
    });
  });
  it("не демо и неполное", () => {
    expect(parseDemoState({ enabled: false })?.videos).toEqual([]);
    expect(parseDemoState(null)).toBeNull();
    expect(parseDemoState({ videos: [] })).toBeNull();
  });
});

describe("fileProblem", () => {
  it("расширение, пустой и слишком большой файл", () => {
    expect(fileProblem("цех.MP4", 100)).toBeNull();
    expect(fileProblem("run.exe", 100)).toMatch(/видеофайл/);
    expect(fileProblem("a.mp4", 0)).toMatch(/пустой/);
    expect(fileProblem("a.mp4", 3 * 1024 ** 3)).toMatch(/больше/);
  });
});

describe("камеры и размеры", () => {
  it("id камеры", () => {
    expect(validCameraId("video-1")).toBe(true);
    expect(validCameraId("плохо")).toBe(false);
    expect(validCameraId("")).toBe(false);
  });
  it("следующий свободный id", () => {
    expect(nextVideoId([])).toBe("video-1");
    expect(nextVideoId(["video-1", "video-2", "cam-1"])).toBe("video-3");
  });
  it("размер", () => {
    expect(sizeLabel(12.34)).toBe("12.3 МБ");
    expect(sizeLabel(2048)).toBe("2.0 ГБ");
  });
});
