import { useState } from "react";
import MjpegImage from "../components/MjpegImage";
import type { ActionError } from "../../app/useEventFeed";
import { areaSuffix } from "../../domain/areas";
import { canMark, type Access } from "../../domain/auth";
import {
  SEVERITY_LABELS,
  bboxStyle,
  countBySeverity,
  countByType,
  formatTime,
  isCritical,
  liveBoxes,
  openViolationsLabel,
  repeatLabel,
  severityOf,
  snapshotSrc,
  sortFeed,
  startedAt,
  statusBadge,
  violatingCameras,
  waitClock,
  waitSeconds,
  waitTone,
} from "../../domain/events";
import {
  VIOLATION_LABELS,
  type Camera,
  type EventStatus,
  type FalseReason,
  type Severity,
  type ViolationEvent,
  type ViolationType,
} from "../../domain/types";
import { isBrowserCamera } from "../../domain/browserCamera";
import BrowserCamera from "../components/BrowserCamera";
import { ChevronDownIcon, SeverityIcon } from "../components/icons";
import { Bump, CountUp, FadeImg, preload, useArrivals } from "../motion";

type FeedFilter = "all" | "new" | "critical";
const FEED_FILTERS: [FeedFilter, string][] = [
  ["all", "Все"],
  ["new", "Новые"],
  ["critical", "Критичные"],
];

interface Props {
  events: ViolationEvent[];
  cameras: Camera[];
  role: Access;
  now: number;
  seen: Record<string, number>;
  actionError: ActionError | null;
  onMark: (id: string, status: EventStatus, reason?: FalseReason) => void;
  onZoom: (src: string) => void;
}

/** Мониторинг оператора: сводка по критичности, камеры с рамками и лента нарушений. */
export default function MonitorView({
  events,
  cameras,
  role,
  now,
  seen,
  actionError,
  onMark,
  onZoom,
}: Props) {
  const [feedFilter, setFeedFilter] = useState<FeedFilter>("all");
  const [showQuiet, setShowQuiet] = useState(false);
  const [live, setLive] = useState<Set<string>>(() => new Set()); // у каких камер пришёл кадр
  const [camOpen, setCamOpen] = useState<string | null>(null); // камера браузера, которую включили здесь

  const counts = countByType(events);
  const bySeverity = countBySeverity(events);
  const cameraName = (id: string) => cameras.find((camera) => camera.id === id)?.name ?? id;
  const alerting = violatingCameras(events, seen, now);

  // критичность типа берём по самому свежему событию этого типа
  const typeSeverity = (type: ViolationType): Severity => {
    const last = events.find((event) => event.type === type);
    return last ? severityOf(last) : "low";
  };
  const allTypes = Object.keys(VIOLATION_LABELS) as ViolationType[];
  const activeTypes = allTypes.filter((type) => counts[type] > 0);
  const quietTypes = allTypes.filter((type) => counts[type] === 0);

  const feedList = sortFeed(events).filter((event) =>
    feedFilter === "new"
      ? event.status === "new"
      : feedFilter === "critical"
        ? isCritical(event)
        : true,
  );
  const arrival = useArrivals(feedList.map((event) => event.id));

  return (
    <>
      <section className="summary" aria-label="Сводка">
        <div className="summary-group">
          {(Object.keys(SEVERITY_LABELS) as Severity[]).map((severity) => (
            <div
              key={severity}
              className={`sev-chip sev-${severity}${bySeverity[severity] > 0 ? " active" : ""}`}
            >
              <SeverityIcon severity={severity} size={bySeverity[severity] > 0 ? 20 : 18} />
              <span>{SEVERITY_LABELS[severity]}</span>
              <b><CountUp value={bySeverity[severity]} /></b>
            </div>
          ))}
        </div>
        <span className="summary-sep" aria-hidden="true" />
        <div className="summary-group">
          {(showQuiet ? allTypes : activeTypes).map((type) => (
            <span
              key={type}
              className={`type-chip sev-${typeSeverity(type)}${counts[type] === 0 ? " zero" : ""}`}
            >
              <span className="dot" aria-hidden="true" />
              {VIOLATION_LABELS[type]} <b><CountUp value={counts[type]} /></b>
            </span>
          ))}
          {quietTypes.length > 0 && (
            <button
              type="button"
              className="more-chip"
              aria-expanded={showQuiet}
              onClick={() => setShowQuiet((v) => !v)}
            >
              {showQuiet
                ? "Скрыть типы без нарушений"
                : `Ещё ${quietTypes.length} типов — без нарушений`}
              <ChevronDownIcon />
            </button>
          )}
        </div>
      </section>

      <div className="monitor">
        <section className="cameras" aria-label="Камеры">
          {cameras.map((camera) => {
            const boxes = liveBoxes(events, seen, camera.id, now);
            const alert = alerting.has(camera.id);
            return (
              <figure key={camera.id} className={`camera${alert ? " alert" : ""}`}>
                <div className="screen">
                  <span className="screen-wait" aria-hidden="true">Нет сигнала · ожидаем видео</span>
                  <MjpegImage
                    src={`/api/cameras/${encodeURIComponent(camera.id)}/mjpeg`}
                    alt=""
                    onLoad={() => setLive((s) => (s.has(camera.id) ? s : new Set(s).add(camera.id)))}
                    onError={(e) => {
                      e.currentTarget.hidden = true;
                    }}
                  />
                  {isBrowserCamera(camera) && (camOpen === camera.id || !live.has(camera.id)) && (
                    <div className="screen-cam">
                      {camOpen === camera.id ? (
                        <BrowserCamera cameraId={camera.id} cameraName={camera.name} autoStart />
                      ) : (
                        <button type="button" onClick={() => setCamOpen(camera.id)}>
                          Включить камеру
                        </button>
                      )}
                    </div>
                  )}
                  {boxes.map((event) => (
                    <div key={event.id} className="bbox" style={bboxStyle(event.bbox)}>
                      <span>{VIOLATION_LABELS[event.type]}</span>
                    </div>
                  ))}
                  {alert && <span className="cam-label">{camera.name}</span>}
                  {alert && boxes[0] && (
                    <span className="cam-alert">
                      <SeverityIcon severity={severityOf(boxes[0])} size={22} />
                      {VIOLATION_LABELS[boxes[0].type]}
                      {repeatLabel(boxes[0]) && <span>{repeatLabel(boxes[0])}</span>}
                    </span>
                  )}
                </div>
                {!alert && (
                  <figcaption>
                    {camera.name}
                    <span className="muted">{openViolationsLabel(events, camera.id)}</span>
                  </figcaption>
                )}
              </figure>
            );
          })}
        </section>

        <aside className="feed" aria-label="Лента нарушений">
          <div className="feed-head">
            <h2>Лента нарушений</h2>
            <div className="segmented" role="group" aria-label="Фильтр ленты">
              {FEED_FILTERS.map(([key, label]) => (
                <button
                  key={key}
                  aria-pressed={feedFilter === key}
                  onClick={() => setFeedFilter(key)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          {feedList.length === 0 && <p className="muted">Пока тихо</p>}
          <ul>
            {feedList.map((event) => {
              const sec = waitSeconds(startedAt(event), now);
              const showTimer =
                sec !== null &&
                (event.status === "new" ||
                  event.status === "confirmed" ||
                  event.status === "sent_to_master");
              const snapshot = snapshotSrc(event);
              return (
                <li
                  key={event.id}
                  className={`event ${event.status} sev-${severityOf(event)}${isCritical(event) ? " critical" : ""} ${arrival(event.id)}`.trimEnd()}
                >
                  <div className="event-row">
                    {snapshot && (
                      <FadeImg
                        className="thumb"
                        src={snapshot}
                        alt="снимок нарушения"
                        fallback={null}
                        onMouseEnter={() => preload(snapshot)}
                        onClick={() => onZoom(snapshot)}
                      />
                    )}
                    <div className="event-info">
                      <div className="event-head">
                        <span className="event-title">
                          <SeverityIcon severity={severityOf(event)} size={16} />
                          {VIOLATION_LABELS[event.type]}
                        </span>
                        {showTimer && (
                          <span className={`timer timer-${waitTone(sec)}`} title="ждёт реакции">
                            {waitClock(startedAt(event), now)}
                          </span>
                        )}
                      </div>
                      <div className="event-meta">
                        {cameraName(event.camera_id)}
                        {areaSuffix(event, cameras)} · {formatTime(event.ts)}
                      </div>
                      <div className="event-tags">
                        <span className={statusBadge(event.status).className}>
                          {statusBadge(event.status).label}
                        </span>
                        {repeatLabel(event) && (
                          <Bump value={event.count} className="repeat" title="повторы инцидента">
                            {repeatLabel(event)}
                          </Bump>
                        )}
                        <span className="muted">
                          уверенность {Math.round(event.confidence * 100)}%
                        </span>
                      </div>
                    </div>
                  </div>
                  {canMark(role) && (event.status === "new" || event.status === "confirmed") && (
                    <div className="actions">
                      {role !== "master" && (
                        <button className="primary" onClick={() => onMark(event.id, "sent_to_master")}>
                          Передать мастеру
                        </button>
                      )}
                      {event.status === "new" && (
                        <button onClick={() => onMark(event.id, "confirmed")}>Подтвердить</button>
                      )}
                      <button className="ghost" onClick={() => onMark(event.id, "false")}>
                        Ложное
                      </button>
                    </div>
                  )}
                  {actionError?.id === event.id && (
                    <p className="action-error" role="alert">
                      {actionError.text}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        </aside>
      </div>
    </>
  );
}
