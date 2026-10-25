import { describe, expect, it } from "vitest";
import {
  cameraErrorMessage,
  deviceLabel,
  frameSize,
  frameStatusMessage,
  frameUrl,
  isBrowserCamera,
  nextBrowserCameraId,
  statusText,
  videoConstraints,
} from "./browserCamera";

describe("browserCamera", () => {
  it("сообщения об ошибках getUserMedia", () => {
    expect(cameraErrorMessage({ name: "NotAllowedError" })).toContain("запрещён");
    expect(cameraErrorMessage({ name: "NotReadableError" })).toContain("закройте другое приложение");
    expect(cameraErrorMessage({ name: "NotFoundError" })).toContain("не найдена");
    expect(cameraErrorMessage({ name: "OverconstrainedError" })).toContain("другое устройство");
    expect(cameraErrorMessage({ name: "Странная", message: "boom" })).toContain("boom");
    expect(cameraErrorMessage(null)).toContain("Не удалось");
  });

  it("без защищённого контекста — про localhost и HTTPS", () => {
    expect(cameraErrorMessage({ name: "NotAllowedError" }, false)).toContain("HTTPS");
  });

  it("размер кадра: до 960 по ширине, пропорции сохраняются", () => {
    expect(frameSize(1920, 1080)).toEqual({ width: 960, height: 540 });
    expect(frameSize(640, 480)).toEqual({ width: 640, height: 480 });
    expect(frameSize(0, 480)).toBeNull();
  });

  it("адрес кадра с именем камеры", () => {
    expect(frameUrl("pc-1")).toBe("/api/phone/pc-1/frame");
    expect(frameUrl(" pc 1 ", "Вебкамера")).toBe(`/api/phone/pc%201/frame?name=${encodeURIComponent("Вебкамера")}`);
    expect(frameUrl("a", "x".repeat(100))).toContain("x".repeat(64));
    expect(frameUrl("a", "x".repeat(100))).not.toContain("x".repeat(65));
  });

  it("ответы сервера", () => {
    expect(frameStatusMessage(204)).toBeNull();
    expect(frameStatusMessage(401)).toContain("войдите");
    expect(frameStatusMessage(403)).toContain("Нет прав");
    expect(frameStatusMessage(500)).toContain("500");
  });

  it("текст статуса", () => {
    expect(statusText(false, 0, 0, 0)).toBe("Камера выключена.");
    expect(statusText(true, 10, 1, 2)).toBe("Идёт отправка. Отправлено кадров: 10 (5.0/с), ошибок: 1.");
    expect(statusText(false, 3, 0, 0)).toContain("Остановлено");
  });

  it("подпись устройства и ограничения", () => {
    expect(deviceLabel({ label: "  HD Cam " }, 0)).toBe("HD Cam");
    expect(deviceLabel({ label: "" }, 1)).toBe("Камера 2");
    expect(JSON.stringify(videoConstraints("abc"))).toContain('"exact":"abc"');
    expect(JSON.stringify(videoConstraints(""))).not.toContain("deviceId");
  });

  it("следующий свободный id", () => {
    expect(nextBrowserCameraId([])).toBe("pc-1");
    expect(nextBrowserCameraId([{ id: "pc-1" }, { id: "pc-3" }])).toBe("pc-2");
  });

  it("камера браузера по id или адресу", () => {
    expect(isBrowserCamera({ id: "pc-1" })).toBe(true);
    expect(isBrowserCamera({ id: "x", source: "http://127.0.0.1:8080/api/phone/x.mjpg" })).toBe(true);
    expect(isBrowserCamera({ id: "cam-1", source: "rtsp://h/1" })).toBe(false);
  });
});
