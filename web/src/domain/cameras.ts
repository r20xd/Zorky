import type { Camera, Metrics } from "./types";

/** Камера «в сети», если последний кадр (метрики воркера) не старше этого. */
export const ONLINE_MS = 10000;

/** Адрес для показа: логин и пароль скрыты, путь свёрнут: rtsp://u:p@host:554/a/b → rtsp://***@host:554/… */
export function maskSource(source: string | undefined): string {
  if (!source) return "—";
  const m = /^([a-z][a-z0-9+.-]*):\/\/(?:[^@/]*@)?([^/?#]+)(.*)$/i.exec(source.trim());
  if (!m) return source; // не URL (путь к демо-видео) — как есть
  const [, scheme, host, rest] = m;
  const hasAuth = /^[a-z][a-z0-9+.-]*:\/\/[^@/]*@/i.test(source.trim());
  return `${scheme}://${hasAuth ? "***@" : ""}${host}${rest ? "/…" : ""}`;
}

export interface CameraState {
  online: boolean;
  fps: number | null;
  delayMs: number | null;
}

export function cameraState(metrics: Metrics | undefined, now: number): CameraState {
  if (!metrics) return { online: false, fps: null, delayMs: null };
  const ts = Date.parse(metrics.ts);
  const online = !Number.isNaN(ts) && now - ts <= ONLINE_MS;
  return { online, fps: metrics.fps, delayMs: metrics.e2e_ms ?? null };
}

/** Проверка формы камеры: пустую строку вместо ошибки не отправляем. */
export function validateCamera(camera: Camera): string | null {
  if (!/^[A-Za-z0-9_-]+$/.test(camera.id)) return "id: латиница, цифры, - и _";
  if (!camera.name.trim()) return "укажите название";
  if (!camera.source?.trim()) return "укажите адрес RTSP";
  return null;
}
