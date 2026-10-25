import { DEVICE_LABELS, formatFps, formatMs, isStale } from "./metrics";
import type { Metrics, MetricsDevice } from "./types";

/** Ответ GET /api/linq: подключение к стенду LinQ. */
export interface LinqStatus {
  state: "connected" | "disconnected";
  busy: boolean;
  host: string;
  device: MetricsDevice;
  devices: MetricsDevice[];
  ready: boolean;
  report: LinqReport | null;
  bench: LinqBench | null;
  log: string[];
}

/** Замеры нашей программы на ускорителе (из репозитория, см. GET /api/linq → bench). */
export interface LinqBench {
  program: string;
  calibrationFrames: number | null;
  latency: { p50: number; p95: number; mean: number } | null;
  fps: { inflight: number; fps: number }[];
  best: { conf: number; f1: number; recall: number; precision: number } | null;
  loaded: { p50: number; fps: number } | null;
}

export interface LinqReport {
  programs: string[];
  broker: boolean;
  modules: Record<string, boolean>;
  checked_at?: string;
}

const DEVICES: MetricsDevice[] = ["cpu", "gpu", "linq"];

const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Разбор поля bench: неполное - null, мусор в числах отбрасывается. */
export function parseBench(raw: unknown): LinqBench | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.program !== "string") return null;
  const lat = r.latency_ms as Record<string, unknown> | null | undefined;
  const latency =
    lat && num(lat.p50) && num(lat.p95) && num(lat.mean)
      ? { p50: lat.p50, p95: lat.p95, mean: lat.mean }
      : null;
  const fpsRaw = r.fps_by_inflight;
  const fps =
    fpsRaw && typeof fpsRaw === "object"
      ? Object.entries(fpsRaw as Record<string, unknown>)
          .map(([k, v]) => ({ inflight: Number(k), fps: v }))
          .filter((x): x is { inflight: number; fps: number } => Number.isFinite(x.inflight) && num(x.fps))
          .sort((a, b) => a.inflight - b.inflight)
      : [];
  const b = r.best as Record<string, unknown> | null | undefined;
  const best =
    b && num(b.conf) && num(b.f1) && num(b.recall) && num(b.precision)
      ? { conf: b.conf, f1: b.f1, recall: b.recall, precision: b.precision }
      : null;
  const l = r.loaded as Record<string, unknown> | null | undefined;
  const ll = l?.latency_ms as Record<string, unknown> | null | undefined;
  const loaded = l && ll && num(ll.p50) && num(l.fps) ? { p50: ll.p50, fps: l.fps } : null;
  return {
    program: r.program,
    calibrationFrames: num(r.calibration_frames) ? r.calibration_frames : null,
    latency,
    fps,
    best,
    loaded,
  };
}

/** Разбор ответа бэкенда: неполный ответ — null, лишнее отбрасывается. */
export function parseLinq(raw: unknown): LinqStatus | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (r.state !== "connected" && r.state !== "disconnected") return null;
  if (!DEVICES.includes(r.device as MetricsDevice)) return null;
  const devices = Array.isArray(r.devices)
    ? r.devices.filter((d): d is MetricsDevice => DEVICES.includes(d as MetricsDevice))
    : DEVICES;
  const rep = r.report as Record<string, unknown> | null | undefined;
  const report: LinqReport | null =
    rep && typeof rep === "object"
      ? {
          programs: Array.isArray(rep.programs)
            ? rep.programs.filter((p): p is string => typeof p === "string")
            : [],
          broker: rep.broker === true,
          modules:
            rep.modules && typeof rep.modules === "object"
              ? Object.fromEntries(Object.entries(rep.modules).map(([k, v]) => [k, v === true]))
              : {},
          checked_at: typeof rep.checked_at === "string" ? rep.checked_at : undefined,
        }
      : null;
  return {
    state: r.state,
    busy: r.busy === true,
    host: typeof r.host === "string" ? r.host : "",
    device: r.device as MetricsDevice,
    devices,
    ready: r.ready === true,
    report,
    bench: parseBench(r.bench),
    log: Array.isArray(r.log) ? r.log.filter((l): l is string => typeof l === "string") : [],
  };
}

export const canControlLinq = (role: string | null) => role === null || role === "admin";

/** Заголовок состояния для карточки. */
export function linqHeadline(s: LinqStatus): { text: string; tone: "ok" | "warn" | "bad" } {
  if (s.busy) return { text: "Идёт подключение…", tone: "warn" };
  if (s.state === "disconnected") return { text: "LinQ не подключён", tone: "bad" };
  return s.ready
    ? { text: "LinQ подключён и готов", tone: "ok" }
    : { text: "Стенд виден, но не готов", tone: "warn" };
}

export interface MeasureRow {
  camera: string;
  device: string;
  fps: string;
  inference: string;
  e2e: string;
  stale: boolean;
  onLinq: boolean;
}

/** Строки таблицы замеров по камерам; устаревшие (воркер молчит) помечены. */
export function measureRows(metrics: Metrics[], now: number): MeasureRow[] {
  return [...metrics]
    .sort((a, b) => a.camera_id.localeCompare(b.camera_id))
    .map((m) => ({
      camera: m.camera_id,
      device: DEVICE_LABELS[m.device],
      fps: formatFps(m.fps),
      inference: formatMs(m.inference_ms),
      e2e: formatMs(m.e2e_ms),
      stale: isStale(m, now),
      onLinq: m.device === "linq",
    }));
}

export interface MeasureTotals {
  cameras: number;
  fps: number;
  inferenceMs: number;
}

/** Итоги по свежим замерам: сумма FPS и средняя задержка инференса; null — свежих нет. */
export function measureTotals(metrics: Metrics[], now: number): MeasureTotals | null {
  const fresh = metrics.filter((m) => !isStale(m, now));
  if (!fresh.length) return null;
  return {
    cameras: fresh.length,
    fps: fresh.reduce((s, m) => s + m.fps, 0),
    inferenceMs: fresh.reduce((s, m) => s + m.inference_ms, 0) / fresh.length,
  };
}

export interface ProgramInfo {
  file: string;
  model: string;
  batch: string;
  preset: string;
  platform: string;
  /** Не образец организаторов, а скомпилированная нами модель. */
  ours: boolean;
}

/** Имя `<модель>_b<батч>_<o1|o5>_<платформа>.tpu` -> части; чужое имя — только файл. */
export function parseProgram(file: string): ProgramInfo {
  const m = /^(.+)_b(\d+)_(o\d)_([a-z0-9]+)\.tpu$/i.exec(file);
  if (!m) return { file, model: file.replace(/\.tpu$/i, ""), batch: "", preset: "", platform: "", ours: false };
  return {
    file,
    model: m[1],
    batch: m[2],
    preset: m[3].toUpperCase(),
    platform: m[4].toUpperCase(),
    ours: !/^(torch_)?resnet/i.test(m[1]),
  };
}

export interface DeviceSummary {
  device: MetricsDevice;
  label: string;
  cameras: number;
  fps: number;
  inferenceMs: number;
}

/** Свежие замеры по устройствам: сколько камер, суммарный FPS и средний инференс. */
export function deviceSummaries(metrics: Metrics[], now: number): DeviceSummary[] {
  const groups = new Map<MetricsDevice, Metrics[]>();
  for (const m of metrics.filter((x) => !isStale(x, now))) {
    groups.set(m.device, [...(groups.get(m.device) ?? []), m]);
  }
  return [...groups.entries()]
    .map(([device, list]) => ({
      device,
      label: DEVICE_LABELS[device],
      cameras: list.length,
      fps: list.reduce((s, m) => s + m.fps, 0),
      inferenceMs: list.reduce((s, m) => s + m.inference_ms, 0) / list.length,
    }))
    .sort((a, b) => b.fps - a.fps);
}

export type NodeTone = "ok" | "warn" | "bad" | "idle";

export interface PipelineNode {
  id: "camera" | "worker" | "broker" | "tpu" | "backend";
  title: string;
  note: string;
  tone: NodeTone;
}

/** Цепочка кадра до ускорителя: у каждого звена — состояние по тому, что известно бэкенду. */
export function pipelineNodes(s: LinqStatus, metrics: Metrics[], now: number): PipelineNode[] {
  const fresh = metrics.filter((m) => !isStale(m, now));
  const onLinq = fresh.filter((m) => m.device === "linq").length;
  const programs = s.report?.programs ?? [];
  const brokerKnown = s.report !== null;
  const tpuTone: NodeTone = onLinq
    ? "ok"
    : s.state === "connected" && programs.length
      ? "warn"
      : s.state === "connected"
        ? "bad"
        : "idle";
  return [
    {
      id: "camera",
      title: "Камеры",
      note: fresh.length ? `${fresh.length} шлют кадры` : "кадров нет",
      tone: fresh.length ? "ok" : "bad",
    },
    {
      id: "worker",
      title: "Воркер",
      note: fresh.length
        ? `считает на ${[...new Set(fresh.map((m) => DEVICE_LABELS[m.device]))].join(" + ")}`
        : "не отвечает",
      tone: fresh.length ? "ok" : "bad",
    },
    {
      id: "broker",
      title: "Брокер RabbitMQ",
      note: !brokerKnown ? "не проверен" : s.report!.broker ? "доступен" : "недоступен",
      tone: !brokerKnown ? "idle" : s.report!.broker ? "ok" : "bad",
    },
    {
      id: "tpu",
      title: "Ускоритель LinQ",
      note: onLinq
        ? `${onLinq} камер на ускорителе`
        : programs.length
          ? `${programs.length} прогр. .tpu, не используется`
          : s.state === "connected"
            ? "нет программ .tpu"
            : "стенд не подключён",
      tone: tpuTone,
    },
    {
      id: "backend",
      title: "Бэкенд и лента",
      note: "рамки, нарушения",
      tone: fresh.length ? "ok" : "idle",
    },
  ];
}
