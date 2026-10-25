/** Демо-видео: свои ролики вместо камер по кругу (ответ GET /api/demo/videos). */
export interface DemoVideo {
  name: string;
  mb: number;
}

export interface DemoPlayer {
  camera_id: string;
  video: string;
}

export interface DemoState {
  enabled: boolean;
  videos: DemoVideo[];
  players: DemoPlayer[];
  folder: string;
}

export const VIDEO_EXTENSIONS = [".mp4", ".avi", ".mkv", ".mov", ".webm"];
export const MAX_VIDEO_MB = 2048;

/** Разбор ответа бэкенда: неполное — null, мусорные элементы списков отбрасываем. */
export function parseDemoState(raw: unknown): DemoState | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.enabled !== "boolean") return null;
  const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
  return {
    enabled: r.enabled,
    folder: typeof r.folder === "string" ? r.folder : "",
    videos: list(r.videos).flatMap((v) => {
      const o = v as Record<string, unknown>;
      return o && typeof o.name === "string" ? [{ name: o.name, mb: typeof o.mb === "number" ? o.mb : 0 }] : [];
    }),
    players: list(r.players).flatMap((p) => {
      const o = p as Record<string, unknown>;
      return o && typeof o.camera_id === "string" && typeof o.video === "string"
        ? [{ camera_id: o.camera_id, video: o.video }]
        : [];
    }),
  };
}

/** Проблема с выбранным файлом перед загрузкой; null — можно грузить. */
export function fileProblem(name: string, sizeBytes: number): string | null {
  const lower = name.toLowerCase();
  if (!VIDEO_EXTENSIONS.some((ext) => lower.endsWith(ext))) return `нужен видеофайл: ${VIDEO_EXTENSIONS.join(", ")}`;
  if (sizeBytes <= 0) return "файл пустой";
  if (sizeBytes > MAX_VIDEO_MB * 1024 * 1024) return `файл больше ${MAX_VIDEO_MB / 1024} ГБ`;
  return null;
}

/** id камеры для видео: латиница, цифры, «-» и «_», до 40 символов. */
export const validCameraId = (id: string) => /^[A-Za-z0-9_.-]{1,40}$/.test(id);

/** Следующий свободный id вида video-N среди занятых (камер и уже играющих). */
export function nextVideoId(taken: string[]): string {
  let n = 1;
  while (taken.includes(`video-${n}`)) n++;
  return `video-${n}`;
}

export const sizeLabel = (mb: number) => (mb >= 1024 ? `${(mb / 1024).toFixed(1)} ГБ` : `${mb.toFixed(1)} МБ`);
