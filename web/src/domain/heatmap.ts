import type { Point, ViolationEvent } from "./types";

export const HEAT_COLS = 32;
export const HEAT_ROWS = 18;
/** Меньше событий — карта и авторазметка ничего не докажут. */
export const MIN_EVENTS = 5;

export interface HeatGrid {
  cols: number;
  rows: number;
  /** сглаженная плотность, по строкам сверху вниз */
  cells: number[];
  max: number;
  /** сколько событий попало в карту */
  count: number;
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

/** Плотность нарушений по кадру: точка человека — середина нижней грани bbox (где он стоит). */
export function buildHeatGrid(events: ViolationEvent[], cols = HEAT_COLS, rows = HEAT_ROWS): HeatGrid {
  const raw = new Array<number>(cols * rows).fill(0);
  let count = 0;
  for (const e of events) {
    const [x1, , x2, y2] = e.bbox ?? [];
    if (![x1, x2, y2].every(Number.isFinite)) continue;
    const c = Math.min(cols - 1, Math.floor(clamp01((x1 + x2) / 2) * cols));
    const r = Math.min(rows - 1, Math.floor(clamp01(y2) * rows));
    raw[r * cols + c] += 1;
    count += 1;
  }
  // сглаживание ядром 3×3 (1-2-1), чтобы точки сливались в пятна
  const kernel = [1, 2, 1];
  const cells = raw.map((_, i) => {
    const r = Math.floor(i / cols);
    const c = i % cols;
    let sum = 0;
    for (let dr = -1; dr <= 1; dr++)
      for (let dc = -1; dc <= 1; dc++) {
        const rr = r + dr;
        const cc = c + dc;
        if (rr < 0 || rr >= rows || cc < 0 || cc >= cols) continue;
        sum += raw[rr * cols + cc] * kernel[dr + 1] * kernel[dc + 1];
      }
    return sum / 16;
  });
  return { cols, rows, cells, max: Math.max(0, ...cells), count };
}

/** Выпуклая оболочка (Эндрю), точки против часовой. */
export function convexHull(points: Point[]): Point[] {
  const pts = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (pts.length < 3) return pts;
  const cross = (o: Point, a: Point, b: Point) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const half = (list: Point[]) => {
    const out: Point[] = [];
    for (const p of list) {
      while (out.length >= 2 && cross(out[out.length - 2], out[out.length - 1], p) <= 0) out.pop();
      out.push(p);
    }
    out.pop();
    return out;
  };
  return [...half(pts), ...half([...pts].reverse())];
}

export interface ZoneSuggestion {
  polygon: Point[];
  /** доля событий карты, пришедшаяся на пятно, 0..1 */
  share: number;
}

const round3 = (v: number) => Math.round(v * 1000) / 1000;

/**
 * Авторазметка опасных зон: горячие пятна карты (плотность ≥ threshold от максимума)
 * превращаются в многоугольники. Мало событий — пустой список.
 */
export function suggestZones(grid: HeatGrid, threshold = 0.4, limit = 4): ZoneSuggestion[] {
  if (grid.count < MIN_EVENTS || grid.max <= 0) return [];
  const { cols, rows, cells } = grid;
  const hot = cells.map((v) => v >= grid.max * threshold);
  const seen = new Array<boolean>(cells.length).fill(false);
  const total = cells.reduce((s, v) => s + v, 0) || 1;
  const found: ZoneSuggestion[] = [];
  for (let start = 0; start < cells.length; start++) {
    if (!hot[start] || seen[start]) continue;
    const stack = [start];
    seen[start] = true;
    const corners: Point[] = [];
    let weight = 0;
    let size = 0;
    while (stack.length) {
      const i = stack.pop() as number;
      const r = Math.floor(i / cols);
      const c = i % cols;
      size += 1;
      weight += cells[i];
      for (const [dx, dy] of [[0, 0], [1, 0], [0, 1], [1, 1]]) corners.push([(c + dx) / cols, (r + dy) / rows]);
      for (const [dr, dc] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const rr = r + dr;
        const cc = c + dc;
        const j = rr * cols + cc;
        if (rr < 0 || rr >= rows || cc < 0 || cc >= cols || !hot[j] || seen[j]) continue;
        seen[j] = true;
        stack.push(j);
      }
    }
    if (size < 2) continue;
    const polygon = convexHull(corners).map(([x, y]): Point => [round3(x), round3(y)]);
    if (polygon.length >= 3) found.push({ polygon, share: weight / total });
  }
  return found.sort((a, b) => b.share - a.share).slice(0, limit);
}
