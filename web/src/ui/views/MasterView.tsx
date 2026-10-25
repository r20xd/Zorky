import { useEffect, useRef, useState } from "react";
import MjpegImage from "../components/MjpegImage";
import { listCameras, listMasterEvents, setEventStatus, subscribeMaster } from "../../infrastructure/api";
import { SEVERITY_LABELS, bboxStyle, formatTime, isCritical, mergeEvent, severityOf, snapshotSrc, startedAt, upsertEvent, waitSeconds, waitTone } from "../../domain/events";
import { CheckIcon, SeverityIcon } from "../components/icons";
import { FadeImg, buzz } from "../motion";
import { vibrate } from "../../infrastructure/alerts";
import { playTone } from "../../infrastructure/sound";
import { areaSuffix, myAreasLabel } from "../../domain/areas";
import { groupMaster, masterTitle, personKey, waitLabel } from "../../domain/master";
import { VIOLATION_LABELS, type Camera, type ViolationEvent } from "../../domain/types";


/** Экран мастера: сначала под телефон. */
export default function MasterView({ areas }: { areas?: string[] }) {
  const [events, setEvents] = useState<ViolationEvent[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [online, setOnline] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // «статус:id» нажатой кнопки — на ней кружок, остальные ждут
  const [now, setNow] = useState(() => Date.now());
  const [unseen, setUnseen] = useState<Set<string>>(new Set());
  const [liveId, setLiveId] = useState<string | null>(null); // карточка с живым потоком: один на экран, браузер держит мало соединений
  const wasOnline = useRef(true);
  const eventsRef = useRef<ViolationEvent[]>([]);
  eventsRef.current = events;

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    document.title = masterTitle(unseen.size);
    return () => {
      document.title = "Zorky";
    };
  }, [unseen]);

  // новые считаются «просмотренными», когда вкладка снова видна
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") setUnseen(new Set());
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);

  // экран не гаснет
  useEffect(() => {
    let lock: WakeLockSentinel | null = null;
    let closed = false;
    const take = () => {
      if (closed || document.visibilityState !== "visible") return;
      navigator.wakeLock?.request("screen").then((l) => (lock = l)).catch(() => undefined);
    };
    take();
    document.addEventListener("visibilitychange", take);
    return () => {
      closed = true;
      document.removeEventListener("visibilitychange", take);
      void lock?.release().catch(() => undefined);
    };
  }, []);

  useEffect(() => {
    const load = () =>
      listMasterEvents()
        .then((list) => {
          setEvents(list);
          setError(null);
        })
        .catch((e: Error) => setError(e.message));
    listCameras().then(setCameras).catch(() => undefined);
    load();
    return subscribeMaster(
      (event) => {
        // тот же человек уже в очереди (падение → неподвижен): карточка одна, сигналим один раз
        const known = eventsRef.current.some(
          (e) => e.id !== event.id && personKey(e) === personKey(event),
        );
        setEvents((list) => mergeEvent(list, event));
        if (known) return;
        setUnseen((s) => new Set(s).add(event.id));
        playTone(isCritical(event) ? 1000 : 700, 500);
        vibrate(isCritical(event) ? [400, 150, 400, 150, 400] : [300]);
      },
      (ok) => {
        setOnline(ok);
        if (!ok && wasOnline.current) {
          playTone(300, 800);
          vibrate([800]);
        }
        // после восстановления связи — подтянуть пропущенное
        if (ok && !wasOnline.current) void load();
        wasOnline.current = ok;
      },
      (event) => setEvents((list) => (list.some((e) => e.id === event.id) ? upsertEvent(list, event) : list)),
    );
  }, []);

  async function resolve(ids: string[], status: "resolved" | "false") {
    setBusy(`${status}:${ids[0]}`);
    try {
      await Promise.all(ids.map((id) => setEventStatus(id, status)));
      buzz();
      setEvents((list) => list.filter((e) => !ids.includes(e.id)));
      setUnseen((s) => {
        const next = new Set(s);
        for (const id of ids) next.delete(id);
        return next;
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const list = groupMaster(events);
  const liveSrc = (cameraId: string) => `/api/cameras/${encodeURIComponent(cameraId)}/mjpeg`;
  const toggleLive = (id: string) => setLiveId((current) => (current === id ? null : id));
  const cameraName = (id: string) => cameras.find((c) => c.id === id)?.name ?? id;

  return (
    <div className="master">
      <p className="muted m-areas">{myAreasLabel(areas)}</p>
      {!online && <div className="offline-banner">нет связи</div>}
      {error && <p className="error">{error}</p>}
      {list.length === 0 && <p className="muted">Нет инцидентов, всё спокойно</p>}
      {list.map(({ main: event, events: grouped }) => {
        const also = [...new Set(grouped.filter((e) => e !== event).map((e) => VIOLATION_LABELS[e.type]))];
        const src = snapshotSrc(event);
        const severity = severityOf(event);
        const isLive = liveId === event.id;
        const sec = waitSeconds(startedAt(event), now) ?? 0;
        return (
          <article key={event.id} className={`m-card${isCritical(event) ? " critical" : ""}`}>
            <div className={`m-banner sev-${severity}`}>
              <SeverityIcon severity={severity} size={34} />
              <div className="m-banner-text">
                <span className="m-sev">{SEVERITY_LABELS[severity]}</span>
                <span className="m-title">{VIOLATION_LABELS[event.type]}</span>
                {also.length > 0 && <span className="m-also">и ещё: {also.join(", ")}</span>}
              </div>
              {grouped.some((e) => unseen.has(e.id)) && <span className="m-new">новое</span>}
            </div>
            {isLive ? (
              <div className="m-frame">
                <MjpegImage src={liveSrc(event.camera_id)} alt="Камера вживую" />
              </div>
            ) : (
              src && (
                <div className="m-frame">
                  <FadeImg src={src} alt="кадр нарушения" loading="eager" />
                  <div className="bbox" style={bboxStyle(event.bbox)} />
                </div>
              )
            )}
            <button className="m-live" aria-pressed={isLive} onClick={() => toggleLive(event.id)}>
              {isLive ? "Показать кадр нарушения" : "Смотреть камеру вживую"}
            </button>
            <div className="m-meta">
              {cameraName(event.camera_id)}
              {areaSuffix(event, cameras)} · зона {event.zone_id ?? "—"} · {formatTime(event.ts)}
            </div>
            <div className={`m-wait timer-${waitTone(sec)}`}>
              <span className="m-wait-label">Ждёт реакции</span>
              <span className="m-wait-value">{waitLabel(startedAt(event), now)}</span>
            </div>
            <div className="m-actions">
              <button
                className={busy === `resolved:${grouped[0].id}` ? "ok busy" : "ok"}
                disabled={busy !== null}
                onClick={() => resolve(grouped.map((e) => e.id), "resolved")}>
                <CheckIcon />
                Принял, иду
              </button>
              <button
                className={busy === `false:${grouped[0].id}` ? "no busy" : "no"}
                disabled={busy !== null}
                onClick={() => resolve(grouped.map((e) => e.id), "false")}>
                Ложное срабатывание
              </button>
            </div>
          </article>
        );
      })}
    </div>
  );
}
