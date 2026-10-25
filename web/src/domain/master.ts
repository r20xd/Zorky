import { isCritical } from "./events";
import type { ViolationEvent } from "./types";

/** Очередь мастера: только sent_to_master. */
export function masterQueue(list: ViolationEvent[]): ViolationEvent[] {
  return list.filter((event) => event.status === "sent_to_master");
}

/** Критичные сверху; внутри группы — давно ждущие выше (по first_seen); порядок стабильный. */
export function sortMaster(list: ViolationEvent[]): ViolationEvent[] {
  return list
    .map((event, i) => ({ event, i }))
    .sort((a, b) => {
      const ca = isCritical(a.event) ? 1 : 0;
      const cb = isCritical(b.event) ? 1 : 0;
      if (ca !== cb) return cb - ca;
      const ta = Date.parse(a.event.first_seen) || 0;
      const tb = Date.parse(b.event.first_seen) || 0;
      return ta - tb || a.i - b.i;
    })
    .map((x) => x.event);
}

/** Очередь для показа: фильтр + сортировка. */
export const masterView = (list: ViolationEvent[]) => sortMaster(masterQueue(list));

/** «Ждёт»: 45 с, 3 мин, 1 ч 05 мин. */
export function waitLabel(since: string, now: number): string {
  const t = Date.parse(since);
  if (Number.isNaN(t)) return "";
  const sec = Math.max(0, Math.floor((now - t) / 1000));
  if (sec < 60) return `${sec} с`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} мин`;
  return `${Math.floor(min / 60)} ч ${String(min % 60).padStart(2, "0")} мин`;
}

/** Заголовок вкладки: «(3) Мастер» при новых. */
export function masterTitle(unseen: number): string {
  return unseen > 0 ? `(${unseen}) Мастер` : "Мастер";
}

export interface MasterGroup {
  /** Главное событие карточки: самое критичное, при равенстве — давно ждущее. */
  main: ViolationEvent;
  /** Все события карточки, включая главное. */
  events: ViolationEvent[];
}

/** Ключ человека на камере: события с одним треком — одно длящееся нарушение (падение → неподвижен). */
export const personKey = (event: ViolationEvent): string =>
  event.track_id == null ? `id:${event.id}` : `${event.camera_id}:${event.track_id}`;

/** Очередь мастера карточками: одно длящееся нарушение — одна карточка, а не по карточке на каждый тип. */
export function groupMaster(list: ViolationEvent[]): MasterGroup[] {
  const groups = new Map<string, ViolationEvent[]>();
  for (const event of masterView(list)) {
    const key = personKey(event);
    groups.set(key, [...(groups.get(key) ?? []), event]);
  }
  return [...groups.values()].map((events) => ({ main: events[0], events }));
}
