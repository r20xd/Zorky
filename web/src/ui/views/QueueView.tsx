import { areaSuffix } from "../../domain/areas";
import { useEffect, useRef, useState } from "react";
import { formatTime, isCritical, severityOf, snapshotSrc, waitTone } from "../../domain/events";
import { SeverityIcon } from "../components/icons";
import { FadeImg, useArrivals } from "../motion";
import ActPdfButton from "../components/ActPdfButton";
import {
  REASON_LABELS, REASON_ORDER, formatWait, keepSelection, moveSelection, parseKey, queueOf, selectionAfterRemoval, waitingMs,
} from "../../domain/queue";
import { VIOLATION_LABELS, type Camera, type FalseReason, type ViolationEvent } from "../../domain/types";

const isTyping = (el: EventTarget | null) =>
  el instanceof HTMLElement && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable);

export default function QueueView({
  events,
  cameras,
  now,
  onConfirm,
  onFalse,
  onHandoff,
  actionError,
}: {
  events: ViolationEvent[];
  cameras: Camera[];
  now: number;
  onConfirm: (id: string) => Promise<void>;
  onFalse: (id: string, reason: FalseReason) => Promise<void>;
  onHandoff: (id: string) => Promise<void>;
  actionError?: { id: string; text: string } | null;
}) {
  const queue = queueOf(events);
  const arrival = useArrivals(queue.map((event) => event.id));
  const [selected, setSelected] = useState<string | null>(null);
  const [reasonFor, setReasonFor] = useState<string | null>(null);
  const [help, setHelp] = useState(false);
  const current = keepSelection(queue, selected);
  const state = useRef({ queue, current, reasonFor });
  state.current = { queue, current, reasonFor };
  const cameraName = (id: string) => cameras.find((c) => c.id === id)?.name ?? id;

  async function decide(run: (id: string) => Promise<void>) {
    const { queue: before, current: id } = state.current;
    if (!id) return;
    setReasonFor(null);
    await run(id);
    // после решения карточка уходит из очереди — выбираем соседнюю
    setSelected(selectionAfterRemoval(before, id, before.filter((e) => e.id !== id)));
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const action = parseKey({
        key: e.key, ctrlKey: e.ctrlKey, altKey: e.altKey, metaKey: e.metaKey,
        typing: isTyping(e.target), reasonOpen: state.current.reasonFor !== null,
      });
      if (!action) return;
      e.preventDefault();
      const { queue: q, current: id } = state.current;
      switch (action.kind) {
        case "move": setSelected(moveSelection(q, id, action.step)); break;
        case "confirm": void decide(onConfirm); break;
        case "false": if (id) setReasonFor(id); break;
        case "handoff": void decide(onHandoff); break;
        case "reason": void decide((i) => onFalse(i, action.reason)); break;
        case "help": setHelp((v) => !v); break;
        case "close": setReasonFor(null); setHelp(false); break;
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {
    if (current) document.getElementById(`q-${current}`)?.scrollIntoView?.({ block: "nearest" });
  }, [current]);

  return (
    <section className="queue">
      <h2>
        Требуют решения: {queue.length}{" "}
        <button type="button" title="Горячие клавиши" onClick={() => setHelp((v) => !v)}>?</button>
      </h2>
      {help && (
        <p className="muted">
          J / K — следующая / предыдущая · 1 — подтвердить · 2 — ложное (затем 1–5 — причина) · 3 — подтвердить и
          передать мастеру · Esc — закрыть · ? — эта подсказка
        </p>
      )}
      {queue.length === 0 && <p className="muted">Очередь пуста</p>}
      <ul>
        {queue.map((event) => (
          <li
            key={event.id}
            id={`q-${event.id}`}
            className={`event new sev-${severityOf(event)}${isCritical(event) ? " critical" : ""}${event.id === current ? " selected" : ""} ${arrival(event.id)}`.trimEnd()}
            onClick={() => setSelected(event.id)}
          >
            <div className="event-head">
              <span className="event-title">
                <SeverityIcon severity={severityOf(event)} size={16} />
                {VIOLATION_LABELS[event.type]}
              </span>
              <span className={`timer timer-${waitTone(waitingMs(event, now) / 1000)}`} title="ждёт решения">
                {formatWait(waitingMs(event, now))}
              </span>
            </div>
            <div className="event-meta">
              {cameraName(event.camera_id)}{areaSuffix(event, cameras)} · {formatTime(event.ts)}
            </div>
            {snapshotSrc(event) && <FadeImg className="thumb" src={snapshotSrc(event)!} alt="снимок нарушения" fallback={null} />}
            <div className="actions">
              <button className="primary" onClick={() => { setSelected(event.id); void decide(onHandoff); }}>
                Подтвердить и передать мастеру
              </button>
              <button onClick={() => { setSelected(event.id); void decide(onConfirm); }}>Подтвердить</button>
              <button className="ghost" onClick={() => { setSelected(event.id); setReasonFor(event.id); }}>Ложное</button>
              <ActPdfButton id={event.id} className="ghost act-pdf" />
            </div>
            {actionError?.id === event.id && <p className="action-error" role="alert">{actionError.text}</p>}
            {reasonFor === event.id && (
              <div className="actions reasons">
                {REASON_ORDER.map((reason, i) => (
                  <button key={reason} onClick={() => { setSelected(event.id); void decide((id) => onFalse(id, reason)); }}>
                    {i + 1}. {REASON_LABELS[reason]}
                  </button>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
