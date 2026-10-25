import { useEffect, useRef, useState } from "react";
import { ACTION_ERROR_MS, actionErrorText } from "../domain/actionError";
import { formatTime, replaceEvent, shouldBeep, upsertEvent } from "../domain/events";
import {
  VIOLATION_LABELS,
  type Camera,
  type EventStatus,
  type FalseReason,
  type ViolationEvent,
} from "../domain/types";
import { listCameras, listEvents, setEventStatus, subscribeEvents } from "../infrastructure/api";
import { playTone } from "../infrastructure/sound";

const NOTICE_MS = 15000;

export interface ActionError {
  id: string;
  text: string;
}

/** Камеры, лента событий (список + SSE), часы для таймеров и действия оператора над событиями. */
export function useEventFeed() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [events, setEvents] = useState<ViolationEvent[]>([]);
  const [online, setOnline] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [actionError, setActionError] = useState<ActionError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // когда событие пришло в этот браузер: по нему гаснут рамки на камерах
  const seen = useRef<Record<string, number>>({});
  const eventsRef = useRef<ViolationEvent[]>([]);
  eventsRef.current = events;

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(timer);
  }, []);

  // связь вернулась после обрыва: события за это время не приходили, берём список заново
  const everOnline = useRef(false);
  const wasOnline = useRef(false);
  function onConnection(ok: boolean) {
    setOnline(ok);
    if (ok && everOnline.current && !wasOnline.current) {
      listEvents()
        .then(setEvents)
        .catch((e: Error) => setError(e.message));
    }
    if (ok) everOnline.current = true;
    wasOnline.current = ok;
  }

  useEffect(() => {
    listCameras()
      .then(setCameras)
      .catch((e: Error) => setError(e.message));
    listEvents()
      .then(setEvents)
      .catch((e: Error) => setError(e.message));
    return subscribeEvents((event, name) => {
      if (name === "violation") {
        seen.current[event.id] = Date.now();
        if (shouldBeep(eventsRef.current, event)) playTone(880, 350);
      }
      if (name === "escalation") {
        const note = (event as ViolationEvent & { note?: string }).note ?? "мастер не ответил";
        setNotice(`${VIOLATION_LABELS[event.type]} · ${formatTime(event.ts)}: ${note}`);
      }
      setEvents((list) => upsertEvent(list, event));
    }, onConnection);
  }, []);

  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(null), NOTICE_MS);
    return () => clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!actionError) return;
    const timer = setTimeout(() => setActionError(null), ACTION_ERROR_MS);
    return () => clearTimeout(timer);
  }, [actionError]);

  async function mark(id: string, status: EventStatus, reason?: FalseReason) {
    setActionError(null);
    try {
      const updated = await setEventStatus(id, status, reason);
      setEvents((list) => replaceEvent(list, updated));
      setError(null);
    } catch (e) {
      setActionError({ id, text: actionErrorText(e) });
    }
  }

  /** Подтвердить и передать мастеру: два PATCH подряд, для оператора — одно действие. */
  async function confirmAndHand(id: string) {
    setActionError(null);
    try {
      const confirmed = await setEventStatus(id, "confirmed");
      setEvents((list) => replaceEvent(list, confirmed));
      const handed = await setEventStatus(id, "sent_to_master");
      setEvents((list) => replaceEvent(list, handed));
      setError(null);
    } catch (e) {
      setActionError({ id, text: actionErrorText(e) });
    }
  }

  return {
    cameras,
    setCameras,
    events,
    online,
    error,
    now,
    seen: seen.current,
    actionError,
    clearActionError: () => setActionError(null),
    notice,
    clearNotice: () => setNotice(null),
    mark,
    confirmAndHand,
  };
}
