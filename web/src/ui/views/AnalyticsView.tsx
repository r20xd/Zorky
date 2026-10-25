import { useEffect, useMemo, useState } from "react";
import MjpegImage from "../components/MjpegImage";
import { getMetrics, listEvents } from "../../infrastructure/api";
import {
  ALL,
  NO_FILTER,
  PERIOD_LABELS,
  SHIFT_LABELS,
  computeAnalytics,
  filterEvents,
  peakHour,
  typeShares,
  zonesOf,
  type AnalyticsFilter,
  type Period,
  type ShiftId,
} from "../../domain/analytics";
import { linqStatus, parseMetrics } from "../../domain/metrics";
import { REASON_LABELS, falseStats } from "../../domain/queue";
import { formatDuration } from "../../domain/journal";
import {
  VIOLATION_LABELS,
  type Camera,
  type Metrics,
  type ViolationEvent,
  type ViolationType,
} from "../../domain/types";
import { Columns, Donut, LineChart, colorAt, type Slice } from "../components/charts";
import HeatCells from "../components/HeatCells";
import { staggerDelay } from "../../domain/motion";
import { buildHeatGrid } from "../../domain/heatmap";
import { plural } from "../../domain/plural";

const pad = (n: number) => String(n).padStart(2, "0");
const HOURS = Array.from({ length: 24 }, (_, h) => h);

function Bars({ rows }: { rows: [string, number][] }) {
  const max = Math.max(1, ...rows.map(([, n]) => n));
  return (
    <ul className="bars">
      {rows.map(([label, n], i) => (
        <li key={label}>
          <span className="bar-label">{label}</span>
          <span className="bar-track">
            <span className="bar-fill" style={{ width: `${(n / max) * 100}%`, animationDelay: `${staggerDelay(i, 30)}ms` }} />
          </span>
          <b>{n}</b>
        </li>
      ))}
    </ul>
  );
}

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: [string, string][];
}) {
  return (
    <label className="filter">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, text]) => (
          <option key={v} value={v}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}

/** Круговая диаграмма по ненулевым категориям; категории без нарушений — в свёрнутом блоке. */
function Split({ title, rows, unit }: { title: string; rows: [string, number][]; unit: [string, string, string] }) {
  const filled = rows.filter(([, n]) => n > 0);
  const empty = rows.filter(([, n]) => n === 0);
  const slices: Slice[] = filled.map(([label, value], i) => ({ label, value, color: colorAt(i) }));
  return (
    <div className="card-panel split-card">
      <h3>{title}</h3>
      {filled.length ? <Donut slices={slices} /> : <p className="empty-note">Нарушений нет</p>}
      {filled.length > 0 && empty.length > 0 && (
        <details className="zero-details">
          <summary>
            Без нарушений: {empty.length} {plural(empty.length, unit)}
          </summary>
          <div className="chips">
            {empty.map(([label]) => (
              <span className="chip-tag" key={label}>
                {label}
              </span>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

export default function AnalyticsView({ cameras }: { cameras: Camera[] }) {
  const [events, setEvents] = useState<ViolationEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<Metrics[] | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [filter, setFilter] = useState<AnalyticsFilter>(NO_FILTER);
  const [from, setFrom] = useState(0);
  const [to, setTo] = useState(24);

  useEffect(() => {
    listEvents(1000)
      .then(setEvents)
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    const load = () => {
      setNow(Date.now());
      getMetrics()
        .then((raw) => setMetrics(parseMetrics(raw)))
        .catch(() => setMetrics(null));
    };
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);

  const set = <K extends keyof AnalyticsFilter>(key: K, value: AnalyticsFilter[K]) =>
    setFilter((f) => ({ ...f, [key]: value }));

  const filtered = useMemo(() => (events ? filterEvents(events, filter, now) : []), [events, filter, now]);
  const timed = useMemo(
    () =>
      filtered.filter((e) => {
        const h = new Date(e.ts).getHours();
        return h >= from && h < to;
      }),
    [filtered, from, to],
  );

  if (error) return <p className="error">{error}</p>;
  if (!events) return <p className="muted">Загрузка…</p>;

  const a = computeAnalytics(filtered);
  const fs = falseStats(filtered);
  const name = (id: string) => cameras.find((c) => c.id === id)?.name ?? id;
  const linq = linqStatus(metrics, now);
  const peak = peakHour(a.byHour);
  const days = Object.entries(a.byDay);
  const heat = buildHeatGrid(filtered);
  const timedShares = typeShares(timed).filter((s) => s.count > 0);
  const shiftOptions = (Object.keys(SHIFT_LABELS) as ShiftId[]).map((s): [string, string] => [s, SHIFT_LABELS[s]]);

  return (
    <section className="analytics">
      <div className="analytics-head">
        <h2>Аналитика</h2>
        <span className={`linq-badge linq-${linq.state}`} title={linq.detail}>
          <span className="linq-dot" />
          {linq.label}
          <small>{linq.detail}</small>
        </span>
      </div>

      <div className="filters">
        <Select
          label="Камера"
          value={filter.camera}
          onChange={(v) => set("camera", v)}
          options={[[ALL, "Все камеры"], ...cameras.map((c): [string, string] => [c.id, c.name])]}
        />
        <Select
          label="Зона"
          value={filter.zone}
          onChange={(v) => set("zone", v)}
          options={[[ALL, "Все зоны"], ...zonesOf(events).map((z): [string, string] => [z, z])]}
        />
        <Select
          label="Период"
          value={filter.period}
          onChange={(v) => set("period", v as Period)}
          options={(Object.keys(PERIOD_LABELS) as Period[]).map((p): [string, string] => [p, PERIOD_LABELS[p]])}
        />
        <Select
          label="Смена"
          value={filter.shift}
          onChange={(v) => set("shift", v as ShiftId | "all")}
          options={[[ALL, "Любая"], ...shiftOptions]}
        />
      </div>

      <div className="kpis">
        <div className="kpi"><b>{a.total}</b><span>нарушений в выборке</span></div>
        <div className="kpi"><b>{Math.round(a.falseShare * 100)}%</b><span>доля ложных</span></div>
        <div className="kpi"><b>{formatDuration(a.avgToMasterMs)}</b><span>до передачи мастеру</span></div>
        <div className="kpi"><b>{formatDuration(a.avgToResolvedMs)}</b><span>от передачи до решения</span></div>
      </div>

      <div className="card-panel">
        <h3>Когда нарушают: нарушения по часам суток</h3>
        {peak !== null && (
          <p className="muted">
            {`Пик — ${pad(peak)}:00 (${a.byHour[peak]}). Если нарушают в одни и те же часы, проверьте свет, нагрузку и смену.`}
          </p>
        )}
        <LineChart
          values={a.byHour}
          labels={HOURS.map((h) => pad(h))}
          highlight={peak}
          labelEvery={2}
          ariaLabel="Нарушения по часам суток"
        />
      </div>

      {days.length > 1 && (
        <div className="card-panel">
          <h3>Динамика по дням</h3>
          <LineChart
            values={days.map(([, n]) => n)}
            labels={days.map(([d]) => d.slice(5))}
            labelEvery={Math.ceil(days.length / 10)}
            ariaLabel="Нарушения по дням"
          />
        </div>
      )}

      <div className="split-grid">
        <Split
          title="По типам"
          rows={(Object.keys(VIOLATION_LABELS) as ViolationType[]).map((t): [string, number] => [
            VIOLATION_LABELS[t],
            a.byType[t],
          ])}
          unit={["тип", "типа", "типов"]}
        />
        <Split
          title="По камерам"
          rows={Object.entries(a.byCamera).map(([id, n]): [string, number] => [name(id), n])}
          unit={["камера", "камеры", "камер"]}
        />
        <Split title="По зонам" rows={Object.entries(a.byZone)} unit={["зона", "зоны", "зон"]} />
        <Split
          title="По сменам"
          rows={(Object.keys(SHIFT_LABELS) as ShiftId[]).map((s): [string, number] => [SHIFT_LABELS[s], a.byShift[s]])}
          unit={["смена", "смены", "смен"]}
        />
      </div>

      <div className="card-panel">
        <h3>Тепловая карта: где в кадре нарушают</h3>
        <p className="muted">
          {filter.camera === ALL
            ? "Выберите камеру — карта наложится на её кадр. Сейчас показаны все камеры вместе."
            : `Камера ${name(filter.camera)}, нарушений: ${heat.count}.`}
        </p>
        <div className="screen heat-stage">
          {filter.camera !== ALL && (
            <MjpegImage
              src={`/api/cameras/${encodeURIComponent(filter.camera)}/mjpeg`}
              alt=""
              draggable={false}
              onError={(e) => {
                e.currentTarget.hidden = true;
              }}
            />
          )}
          <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="zone-svg">
            <HeatCells grid={heat} />
          </svg>
        </div>
      </div>

      <div className="card-panel">
        <h3>Какие нарушения в выбранное время</h3>
        <div className="filters">
          <Select
            label="С"
            value={String(from)}
            onChange={(v) => {
              const n = Number(v);
              setFrom(n);
              if (to <= n) setTo(n + 1);
            }}
            options={HOURS.map((h): [string, string] => [String(h), `${pad(h)}:00`])}
          />
          <Select
            label="До"
            value={String(to)}
            onChange={(v) => setTo(Number(v))}
            options={HOURS.filter((h) => h >= from).map((h): [string, string] => [String(h + 1), `${pad(h + 1)}:00`])}
          />
        </div>
        <p className="muted">
          Камера: {filter.camera === ALL ? "все" : name(filter.camera)} · время {pad(from)}:00–{pad(to)}:00 ·
          нарушений: {timed.length}
        </p>
        {timedShares.length ? (
          <Columns
            ariaLabel="Нарушения по типам в выбранное время"
            items={timedShares.map((s, i) => ({
              label: VIOLATION_LABELS[s.type],
              value: s.count,
              share: s.share,
              color: colorAt(i),
            }))}
          />
        ) : (
          <p className="empty-note">В это время нарушений нет</p>
        )}
      </div>

      <div className="split-grid">
        <div className="card-panel">
          <h3>Ложные по причинам</h3>
          <Bars
            rows={Object.entries(fs.byReason).map(([r, n]): [string, number] => [
              r === "none" ? "Без причины" : ((REASON_LABELS as Record<string, string>)[r] ?? r),
              n,
            ])}
          />
        </div>
        <div className="card-panel">
          <h3>Доля ложных по камерам, %</h3>
          <Bars
            rows={Object.entries(fs.byCamera).map(([id, c]): [string, number] => [
              `${name(id)} (${c.falses} из ${c.total})`,
              Math.round(c.share * 100),
            ])}
          />
        </div>
      </div>
    </section>
  );
}
