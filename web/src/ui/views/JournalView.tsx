import { REASON_LABELS } from "../../domain/queue";
import { useEffect, useState } from "react";
import { areaOptions, areaSuffix, filterByArea } from "../../domain/areas";
import { fetchJournal } from "../../infrastructure/api";
import { STATUS_LABELS, snapshotSrc } from "../../domain/events";
import ActPdfButton from "../components/ActPdfButton";
import { FadeImg, Lightbox, preload, useLingering } from "../motion";
import { staggerDelay } from "../../domain/motion";
import {
  EMPTY_FILTERS,
  PAGE_SIZE,
  csvUrl,
  formatDuration,
  historyRows,
  pageCount,
  reactionChange,
  reactionMs,
  type JournalFilters,
} from "../../domain/journal";
import {
  VIOLATION_LABELS,
  type Camera,
  type EventStatus,
  type ViolationEvent,
  type ViolationType,
} from "../../domain/types";

export default function JournalView({ cameras }: { cameras: Camera[] }) {
  const [filters, setFilters] = useState<JournalFilters>(EMPTY_FILTERS);
  const [area, setArea] = useState(""); // участка в API журнала нет: фильтр по камерам на клиенте
  const [page, setPage] = useState(0);
  const [rows, setRows] = useState<ViolationEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<ViolationEvent | null>(null);
  const shownEvent = useLingering(open);
  const [filtersOpen, setFiltersOpen] = useState(false); // выдвижная панель (телефон)
  const [loading, setLoading] = useState(false);
  const [batch, setBatch] = useState(0); // номер загрузки: новая страница строк проявляется лесенкой заново

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetchJournal(filters, PAGE_SIZE, page * PAGE_SIZE)
      .then((result) => {
        if (!alive) return;
        setRows(result.events);
        setTotal(result.total);
        setError(null);
        setBatch((n) => n + 1);
      })
      .catch((e: Error) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [filters, page]);

  const set = <K extends keyof JournalFilters>(key: K, value: JournalFilters[K]) => {
    setPage(0);
    setFilters((f) => ({ ...f, [key]: value }));
  };
  const shown = filterByArea(rows, cameras, area);
  const cameraName = (id: string) => cameras.find((c) => c.id === id)?.name ?? id;

  return (
    <section className="journal">
      <button className="filters-toggle" onClick={() => setFiltersOpen(true)}>
        Фильтры
      </button>
      {filtersOpen && <div className="filters-backdrop" onClick={() => setFiltersOpen(false)} />}
      <div className={filtersOpen ? "filters open" : "filters"}>
        <button className="filters-close" onClick={() => setFiltersOpen(false)}>
          Готово
        </button>
        <select aria-label="Тип нарушения" value={filters.type} onChange={(e) => set("type", e.target.value as ViolationType | "")}>
          <option value="">Все типы</option>
          {(Object.keys(VIOLATION_LABELS) as ViolationType[]).map((t) => (
            <option key={t} value={t}>
              {VIOLATION_LABELS[t]}
            </option>
          ))}
        </select>
        <select aria-label="Камера" value={filters.camera} onChange={(e) => set("camera", e.target.value)}>
          <option value="">Все камеры</option>
          {cameras.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <select aria-label="Участок" value={area} onChange={(e) => setArea(e.target.value)}>
          <option value="">Все участки</option>
          {areaOptions(cameras).map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        <input placeholder="Зона" value={filters.zone} onChange={(e) => set("zone", e.target.value)} />
        <select aria-label="Статус" value={filters.status} onChange={(e) => set("status", e.target.value as EventStatus | "")}>
          <option value="">Все статусы</option>
          {(Object.keys(STATUS_LABELS) as EventStatus[]).map((s) => (
            <option key={s} value={s}>
              {STATUS_LABELS[s]}
            </option>
          ))}
        </select>
        <label>
          с <input type="datetime-local" value={filters.from} onChange={(e) => set("from", e.target.value)} />
        </label>
        <label>
          по <input type="datetime-local" value={filters.to} onChange={(e) => set("to", e.target.value)} />
        </label>
        <a className="button" href={csvUrl(filters)} download="events.csv">
          Скачать CSV
        </a>
      </div>
      {error && <p className="error">{error}</p>}
      <div className={loading ? "table-wrap is-loading" : "table-wrap"} aria-busy={loading}>
      <table>
        <thead>
          <tr>
            <th>Время</th>
            <th>Тип</th>
            <th>Камера</th>
            <th>Участок</th>
            <th>Зона</th>
            <th>Статус</th>
            <th>Реакция</th>
            <th>Акт</th>
          </tr>
        </thead>
        <tbody key={batch}>
          {shown.map((e, i) => (
            <tr
              key={e.id}
              onClick={() => setOpen(e)}
              onMouseEnter={() => preload(snapshotSrc(e))}
              className="clickable fx-row"
              style={{ animationDelay: `${staggerDelay(i, 20)}ms` }}
            >
              <td>{new Date(e.ts).toLocaleString("ru-RU", { hour12: false })}</td>
              <td>{VIOLATION_LABELS[e.type]}</td>
              <td>{cameraName(e.camera_id)}</td>
              <td>{areaSuffix(e, cameras).slice(3) || "—"}</td>
              <td>{e.zone_id ?? "—"}</td>
              <td>{STATUS_LABELS[e.status]}</td>
              <td>{formatDuration(reactionMs(e))}</td>
              <td>
                <ActPdfButton id={e.id} className="act-link" label="PDF" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      <ul key={batch} className={loading ? "cards is-loading" : "cards"}>
        {shown.map((e, i) => (
          <li
            key={e.id}
            className="card fx-row"
            style={{ animationDelay: `${staggerDelay(i, 20)}ms` }}
            onClick={() => setOpen(e)}
            onMouseEnter={() => preload(snapshotSrc(e))}
          >
            <b>{VIOLATION_LABELS[e.type]}</b>
            <span className="muted">{new Date(e.ts).toLocaleString("ru-RU", { hour12: false })}</span>
            <span>
              {cameraName(e.camera_id)}{areaSuffix(e, cameras)} · {e.zone_id ?? "—"}
            </span>
            <span>
              {STATUS_LABELS[e.status]} · реакция {formatDuration(reactionMs(e))}
            </span>
            <ActPdfButton id={e.id} className="act-link" />
          </li>
        ))}
      </ul>
      {shown.length === 0 && <p className="muted">Ничего не найдено</p>}
      <div className="pager">
        <button disabled={page === 0} onClick={() => setPage(page - 1)}>
          ←
        </button>
        <span>
          {page + 1} / {pageCount(total)} · всего {total}
        </span>
        <button disabled={page + 1 >= pageCount(total)} onClick={() => setPage(page + 1)}>
          →
        </button>
      </div>
      <Lightbox open={open !== null} onClose={() => setOpen(null)} label="Карточка события">
        {shownEvent && (
          <div className="detail" onClick={(ev) => ev.stopPropagation()}>
            {snapshotSrc(shownEvent) ? (
              <FadeImg src={snapshotSrc(shownEvent)!} alt="кадр нарушения" loading="eager" />
            ) : (
              <p className="muted">Кадра нет</p>
            )}
            <h3>
              {VIOLATION_LABELS[shownEvent.type]} · {cameraName(shownEvent.camera_id)}{areaSuffix(shownEvent, cameras)}
            </h3>
            <dl className="evidence">
              <dt>Время</dt>
              <dd>{new Date(shownEvent.ts).toLocaleString("ru-RU", { hour12: false })}</dd>
              <dt>Зона</dt>
              <dd>{shownEvent.zone_id ?? "—"}</dd>
              <dt>Повторов</dt>
              <dd>{shownEvent.count}</dd>
              <dt>Реакция</dt>
              <dd>
                {reactionChange(shownEvent)
                  ? `${reactionChange(shownEvent)!.by}, через ${formatDuration(reactionMs(shownEvent))}`
                  : "реакции не было"}
              </dd>
              <dt>Номер записи</dt>
              <dd>{shownEvent.id}</dd>
            </dl>
            <ul>
              {historyRows(shownEvent).map(({ change, afterMs }, i) => (
                <li key={i}>
                  {new Date(change.ts).toLocaleString("ru-RU", { hour12: false })} ·{" "}
                  {STATUS_LABELS[change.status]}{change.reason ? ` (${REASON_LABELS[change.reason]})` : ""} · {change.by} · через {formatDuration(afterMs)}
                </li>
              ))}
              {shownEvent.status_history.length === 0 && <li className="muted">Статус не менялся</li>}
            </ul>
            <ActPdfButton id={shownEvent.id} />
          </div>
        )}
      </Lightbox>
    </section>
  );
}
