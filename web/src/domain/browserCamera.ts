// Камера, подключённая через браузер (вебкамера компьютера или телефона): чистая логика без DOM.
import type { Camera } from "./types";

export const BROWSER_CAM_ID = "pc-1";
export const BROWSER_CAM_NAME = "Вебкамера компьютера";
export const BROWSER_CAM_FPS = 5;
export const BROWSER_CAM_MAX_WIDTH = 960;
export const BROWSER_CAM_QUALITY = 0.7;

/** Камера, кадры которой присылает браузер: заведена через /api/phone/… */
export function isBrowserCamera(camera: Pick<Camera, "id" | "source">): boolean {
  return /^(pc|phone)-/.test(camera.id) || (camera.source ?? "").includes("/api/phone/");
}

/** Свободный id для новой камеры браузера: pc-1, pc-2… */
export function nextBrowserCameraId(cameras: Pick<Camera, "id">[]): string {
  const used = new Set(cameras.map((camera) => camera.id));
  let n = 1;
  while (used.has(`pc-${n}`)) n++;
  return `pc-${n}`;
}

/** Адрес отправки кадра; имя нужно, чтобы камера завелась с понятным названием. */
export function frameUrl(cameraId: string, name?: string): string {
  const base = `/api/phone/${encodeURIComponent(cameraId.trim())}/frame`;
  const trimmed = (name ?? "").trim().slice(0, 64);
  return trimmed ? `${base}?name=${encodeURIComponent(trimmed)}` : base;
}

/** Размер кадра: ширина не больше maxWidth, пропорции сохраняются. */
export function frameSize(
  videoWidth: number,
  videoHeight: number,
  maxWidth = BROWSER_CAM_MAX_WIDTH,
): { width: number; height: number } | null {
  if (!(videoWidth > 0) || !(videoHeight > 0)) return null;
  const width = Math.min(maxWidth, Math.round(videoWidth));
  return { width, height: Math.max(1, Math.round((videoHeight / videoWidth) * width)) };
}

/** Понятное сообщение по ошибке getUserMedia. */
export function cameraErrorMessage(err: unknown, secure = true): string {
  if (!secure) {
    return "Браузер даёт доступ к камере только на защищённой странице: откройте сайт как localhost или по HTTPS.";
  }
  const name = (err as { name?: string } | null)?.name ?? "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
    case "PermissionDeniedError":
      return "Доступ к камере запрещён. Разрешите камеру для этого сайта в настройках браузера (значок слева в адресной строке) и нажмите «Включить» снова.";
    case "NotReadableError":
    case "TrackStartError":
      return "Камера занята: закройте другое приложение, использующее камеру, и попробуйте снова.";
    case "NotFoundError":
    case "DevicesNotFoundError":
      return "Камера не найдена: подключите камеру или выберите другое устройство.";
    case "OverconstrainedError":
    case "ConstraintNotSatisfiedError":
      return "Выбранная камера недоступна: выберите другое устройство.";
    case "AbortError":
      return "Камера не запустилась. Попробуйте ещё раз.";
    case "TypeError":
      return "Браузер не умеет работать с камерой на этой странице: откройте сайт как localhost или по HTTPS.";
    default: {
      const message = (err as { message?: string } | null)?.message;
      return `Не удалось включить камеру${message ? `: ${message}` : "."}`;
    }
  }
}

/** Сообщение по коду ответа сервера на кадр; null — всё хорошо (204). */
export function frameStatusMessage(status: number): string | null {
  if (status === 204) return null;
  if (status === 401) return "Сессия закончилась: войдите в систему и включите камеру снова.";
  if (status === 403) return "Нет прав: камеру могут подключать администратор, оператор и специалист.";
  if (status === 400) return "Сервер не принял кадр.";
  return `Сервер ответил ${status}.`;
}

/** Строка состояния: «Отправлено кадров: 12 (4.8/с), ошибок: 0». */
export function statusText(running: boolean, sent: number, failed: number, elapsedSec: number): string {
  if (!running && sent === 0 && failed === 0) return "Камера выключена.";
  const rate = sent / Math.max(1, elapsedSec);
  return `${running ? "Идёт отправка" : "Остановлено"}. Отправлено кадров: ${sent} (${rate.toFixed(1)}/с), ошибок: ${failed}.`;
}

/** Подпись устройства; у скрытых названий (до разрешения) — «Камера N». */
export function deviceLabel(device: { label: string }, index: number): string {
  return device.label.trim() || `Камера ${index + 1}`;
}

/** Ограничения для getUserMedia: выбранное устройство или любая камера. */
export function videoConstraints(deviceId: string): MediaStreamConstraints {
  return {
    video: {
      ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
      width: { ideal: 1280 },
      height: { ideal: 720 },
    },
    audio: false,
  };
}
