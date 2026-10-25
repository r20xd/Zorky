import { sortFeed, startedAt } from "./events";
import type { FalseReason, ViolationEvent } from "./types";

export const REASON_LABELS: Record<FalseReason, string> = {
  not_person: "Не человек",
  ppe_present: "СИЗ на месте",
  wrong_zone: "Не та зона",
  duplicate: "Повтор",
  other: "Другое",
};
/** Порядок причин: клавиши 1..5 в открытом меню. */
export const REASON_ORDER: FalseReason[] = ["not_person", "ppe_present", "wrong_zone", "duplicate", "other"];

export type QueueAction =
  | { kind: "move"; step: 1 | -1 }
  | { kind: "confirm" }
  | { kind: "false" }
  | { kind: "handoff" }
  | { kind: "help" }
  | { kind: "reason"; reason: FalseReason }
  | { kind: "close" };

export interface KeyInfo {
  key: string;
  ctrlKey?: boolean;
  altKey?: boolean;
  metaKey?: boolean;
  /** фокус в поле ввода (input, textarea, select, contenteditable) */
  typing?: boolean;
  /** открыто меню причин: цифры 1..5 выбирают причину */
  reasonOpen?: boolean;
}

/** Разбор клавиши очереди; null — не наша клавиша. В полях ввода и с модификаторами не срабатывает. */
export function parseKey(k: KeyInfo): QueueAction | null {
  if (k.typing || k.ctrlKey || k.altKey || k.metaKey) return null;
  if (k.reasonOpen) {
    if (k.key === "Escape") return { kind: "close" };
    const reason = REASON_ORDER[Number(k.key) - 1];
    if (/^[1-9]$/.test(k.key) && reason) return { kind: "reason", reason };
  }
  switch (k.key.toLowerCase()) {
    case "j":
    case "о": // та же клавиша в русской раскладке
      return { kind: "move", step: 1 };
    case "k":
    case "л":
      return { kind: "move", step: -1 };
    case "1":
      return { kind: "confirm" };
    case "2":
      return { kind: "false" };
    case "3":
      return { kind: "handoff" };
    case "?":
      return { kind: "help" };
    case "escape":
      return { kind: "close" };
    default:
      return null;
  }
}

/** Очередь: только `new`, критичные сверху. */
export const queueOf = (events: ViolationEvent[]): ViolationEvent[] =>
  sortFeed(events.filter((e) => e.status === "new"));

/** Сколько ждёт решения, мс (от first_seen, а без него — от времени события). */
export function waitingMs(event: ViolationEvent, now: number): number {
  const ms = now - Date.parse(startedAt(event));
  return Number.isNaN(ms) ? 0 : Math.max(0, ms);
}

export function formatWait(ms: number): string {
  const sec = Math.floor(ms / 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return sec >= 3600
    ? `${Math.floor(sec / 3600)}:${pad(Math.floor(sec / 60) % 60)}:${pad(sec % 60)}`
    : `${pad(Math.floor(sec / 60))}:${pad(sec % 60)}`;
}

/** Id выбранной карточки после шага J/K (без зацикливания); нет текущей — первая. */
export function moveSelection(queue: ViolationEvent[], currentId: string | null, step: 1 | -1): string | null {
  if (queue.length === 0) return null;
  const i = queue.findIndex((e) => e.id === currentId);
  if (i < 0) return queue[0].id;
  return queue[Math.min(queue.length - 1, Math.max(0, i + step))].id;
}

/** Что выбрать, когда карточка ушла из очереди: на её месте следующая, иначе предыдущая, иначе ничего. */
export function selectionAfterRemoval(before: ViolationEvent[], removedId: string, after: ViolationEvent[]): string | null {
  const i = before.findIndex((e) => e.id === removedId);
  const ids = new Set(after.map((e) => e.id));
  for (const e of before.slice(i + 1)) if (ids.has(e.id)) return e.id;
  for (const e of before.slice(0, Math.max(0, i)).reverse()) if (ids.has(e.id)) return e.id;
  return after[0]?.id ?? null;
}

/** Выбор после обновления списка: текущая жива — остаётся, иначе первая. */
export function keepSelection(queue: ViolationEvent[], currentId: string | null): string | null {
  return queue.some((e) => e.id === currentId) ? currentId : (queue[0]?.id ?? null);
}

/** Причина ложного: последняя в истории статусов. */
export function reasonOf(event: ViolationEvent): FalseReason | null {
  for (let i = event.status_history.length - 1; i >= 0; i--) {
    const reason = event.status_history[i].reason;
    if (reason) return reason;
  }
  return null;
}

export interface FalseStats {
  /** ложные по причинам; без указанной причины — ключ "none" */
  byReason: Record<string, number>;
  /** по камерам: ложных, всего, доля 0..1 */
  byCamera: Record<string, { falses: number; total: number; share: number }>;
}

export function falseStats(events: ViolationEvent[]): FalseStats {
  const byReason: Record<string, number> = {};
  const cams: Record<string, { falses: number; total: number }> = {};
  for (const e of events) {
    const c = (cams[e.camera_id] ??= { falses: 0, total: 0 });
    c.total += 1;
    if (e.status !== "false") continue;
    c.falses += 1;
    const key = reasonOf(e) ?? "none";
    byReason[key] = (byReason[key] ?? 0) + 1;
  }
  const byCamera = Object.fromEntries(
    Object.entries(cams)
      .filter(([, c]) => c.falses > 0)
      .map(([id, c]) => [id, { ...c, share: c.falses / c.total }]),
  );
  return { byReason, byCamera };
}
