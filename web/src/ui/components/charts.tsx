import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { staggerDelay } from "../../domain/motion";

export const CHART_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
  "var(--chart-7)",
  "var(--chart-8)",
];
export const colorAt = (i: number) => CHART_COLORS[i % CHART_COLORS.length];

/** Верхняя граница оси: 1, 2, 5 × 10^k, чтобы деления были круглыми. */
export function niceMax(value: number): number {
  if (value <= 1) return 1;
  const pow = 10 ** Math.floor(Math.log10(value));
  const n = value / pow;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * pow;
}

interface LineProps {
  values: number[];
  labels: string[];
  /** индекс точки, которую подсветить (пик) */
  highlight?: number | null;
  /** минимальный шаг подписей оси X (реальный шаг считается по ширине) */
  labelEvery?: number;
  ariaLabel: string;
  /** подпись, когда все значения нулевые */
  emptyText?: string;
}

export const CHART_HEIGHT = 240;

/** Ширина контейнера в пикселях; без ResizeObserver (тесты) — запасное значение. */
export function useWidth<T extends HTMLElement>(fallback = 640) {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => {
      const w = Math.round(el.getBoundingClientRect().width);
      if (w > 0) setWidth(w);
    };
    update();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

/** Шаг подписей оси X: чтобы подпись шириной labelPx не налезала на соседнюю. */
export function labelStep(count: number, innerW: number, labelPx = 34, min = 1): number {
  if (count <= 1) return 1;
  const slot = innerW / (count - 1);
  return Math.max(min, Math.ceil(labelPx / Math.max(1, slot)));
}

export function LineChart({ values, labels, highlight = null, labelEvery = 1, ariaLabel, emptyText = "Нарушений в выборке нет" }: LineProps) {
  const [ref, W] = useWidth<HTMLDivElement>();
  const H = CHART_HEIGHT;
  const pad = { l: 32, r: 14, t: 14, b: 28 };
  const empty = values.every((v) => v === 0);
  const max = empty ? 4 : niceMax(Math.max(0, ...values));
  const innerW = Math.max(60, W - pad.l - pad.r);
  const innerH = H - pad.t - pad.b;
  const x = (i: number) => pad.l + (values.length <= 1 ? innerW / 2 : (i / (values.length - 1)) * innerW);
  const y = (v: number) => pad.t + innerH - (v / max) * innerH;
  const points = values.map((v, i) => `${x(i)},${y(v)}`);
  const area = values.length
    ? `M${x(0)},${y(0)} L${points.join(" L")} L${x(values.length - 1)},${y(0)} Z`
    : "";
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((k) => Math.round(max * k * 100) / 100);
  const step = labelStep(values.length, innerW, 36, labelEvery);
  return (
    <div className="chart-box" ref={ref}>
      <svg
        className={empty ? "chart line-chart is-empty" : "chart line-chart"}
        width={W}
        height={H}
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={ariaLabel}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid" x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} />
            {(!empty || t === 0) && (
              <text className="axis" x={pad.l - 6} y={y(t) + 4} textAnchor="end">
                {t}
              </text>
            )}
          </g>
        ))}
        {empty ? (
          <text className="chart-empty" x={W / 2} y={pad.t + innerH / 2} textAnchor="middle">
            {emptyText}
          </text>
        ) : (
          <>
            <path className="area" d={area} />
            <polyline className="line" points={points.join(" ")} fill="none" pathLength={1} />
          </>
        )}
        {values.map((v, i) => (
          <g key={labels[i] ?? i}>
            {!empty && (
              <circle
                className={i === highlight ? "dot peak" : "dot"}
                style={{ animationDelay: `${staggerDelay(i, 600 / Math.max(1, values.length), values.length)}ms` }}
                cx={x(i)}
                cy={y(v)}
                r={i === highlight ? 4.5 : 2.5}
              >
                <title>{`${labels[i]}: ${v}`}</title>
              </circle>
            )}
            {i % step === 0 && (
              <text className="axis" x={x(i)} y={H - 8} textAnchor="middle">
                {labels[i]}
              </text>
            )}
          </g>
        ))}
      </svg>
    </div>
  );
}

export interface Slice {
  label: string;
  value: number;
  color: string;
}

export function Donut({ slices, center }: { slices: Slice[]; center?: ReactNode }) {
  const total = slices.reduce((s, x) => s + x.value, 0);
  const R = 56;
  const C = 2 * Math.PI * R;
  let offset = 0;
  return (
    <div className="donut">
      <svg viewBox="0 0 160 160" role="img" aria-label={`Всего ${total}`}>
        <circle className="donut-bg" cx="80" cy="80" r={R} />
        {total > 0 &&
          slices.map((s) => {
            const len = (s.value / total) * C;
            const el = (
              <circle
                key={s.label}
                cx="80"
                cy="80"
                r={R}
                fill="none"
                stroke={s.color}
                strokeWidth="22"
                strokeDasharray={`${len} ${C - len}`}
                strokeDashoffset={-offset}
                transform="rotate(-90 80 80)"
              >
                <title>{`${s.label}: ${s.value} (${Math.round((s.value / total) * 100)}%)`}</title>
              </circle>
            );
            offset += len;
            return el;
          })}
        <text className="donut-total" x="80" y="82" textAnchor="middle">
          {total}
        </text>
        <text className="axis" x="80" y="100" textAnchor="middle">
          {center ?? "всего"}
        </text>
      </svg>
      <ul className="legend">
        {slices.map((s) => (
          <li key={s.label}>
            <span className="swatch" style={{ background: s.color }} />
            <span className="legend-label">{s.label}</span>
            <b>{s.value}</b>
            <span className="muted">{total ? Math.round((s.value / total) * 100) : 0}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export interface Column {
  label: string;
  value: number;
  share: number;
  color: string;
}

export function Columns({ items, ariaLabel }: { items: Column[]; ariaLabel: string }) {
  const max = niceMax(Math.max(0, ...items.map((i) => i.value)));
  return (
    <div className="columns" role="img" aria-label={ariaLabel}>
      {items.map((c, j) => (
        <div className="col" key={c.label} title={`${c.label}: ${c.value} (${Math.round(c.share * 100)}%)`}>
          <span className="col-value">
            {c.value} <small>{Math.round(c.share * 100)}%</small>
          </span>
          <span className="col-track">
            <span className="col-fill" style={{ height: `${(c.value / max) * 100}%`, background: c.color, animationDelay: `${staggerDelay(j, 40)}ms` }} />
          </span>
          <span className="col-label">{c.label}</span>
        </div>
      ))}
    </div>
  );
}
