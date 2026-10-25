import type { Metrics, MetricsDevice } from "./types";

export const DEVICE_LABELS: Record<MetricsDevice, string> = {
  linq: "LinQ",
  cpu: "CPU",
  gpu: "GPU",
};

/** Метрики старше этого считаем устаревшими (воркер молчит). */
export const STALE_MS = 15000;

/** Разбор ответа GET /api/metrics: берёт только корректные записи, остальное молча отбрасывает. */
export function parseMetrics(raw: unknown): Metrics[] {
  if (!Array.isArray(raw)) return [];
  const result: Metrics[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const m = item as Record<string, unknown>;
    if (
      typeof m.camera_id !== "string" ||
      (m.device !== "linq" && m.device !== "cpu" && m.device !== "gpu") ||
      typeof m.fps !== "number" ||
      typeof m.inference_ms !== "number" ||
      typeof m.ts !== "string"
    )
      continue;
    result.push({
      camera_id: m.camera_id,
      device: m.device,
      fps: m.fps,
      inference_ms: m.inference_ms,
      e2e_ms: typeof m.e2e_ms === "number" ? m.e2e_ms : undefined,
      ts: m.ts,
    });
  }
  return result;
}

export function isStale(metrics: Metrics, now: number): boolean {
  const ts = Date.parse(metrics.ts);
  return Number.isNaN(ts) || now - ts > STALE_MS;
}

export function formatMs(value: number | undefined): string {
  return value === undefined ? "—" : `${Math.round(value)} мс`;
}

export function formatFps(value: number): string {
  return value.toFixed(1);
}

export type LinqState = "connected" | "fallback" | "offline" | "unknown";

export interface LinqStatus {
  state: LinqState;
  label: string;
  detail: string;
}

/** Подключён ли сейчас LinQ: хотя бы одна свежая запись метрик с device=linq. */
export function linqStatus(metrics: Metrics[] | null, now: number): LinqStatus {
  if (metrics === null)
    return { state: "unknown", label: "LinQ: нет данных", detail: "Метрики доступны ролям manager и admin" };
  const fresh = metrics.filter((m) => !isStale(m, now));
  const linq = fresh.filter((m) => m.device === "linq");
  if (linq.length)
    return {
      state: "connected",
      label: "LinQ подключён",
      detail: `Камер на LinQ: ${linq.length} из ${fresh.length}`,
    };
  if (fresh.length) {
    const devices = [...new Set(fresh.map((m) => DEVICE_LABELS[m.device]))].join(", ");
    return { state: "fallback", label: "LinQ не подключён", detail: `Инференс идёт на ${devices}` };
  }
  return { state: "offline", label: "Воркеры не отвечают", detail: "Свежих метрик нет — LinQ не проверить" };
}
