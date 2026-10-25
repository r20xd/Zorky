import type { HeatGrid } from "../../domain/heatmap";

/** Тепловая карта поверх кадра: SVG в viewBox 0 0 1 1, цвет от синего через жёлтый к красному. */
export default function HeatCells({ grid }: { grid: HeatGrid }) {
  if (grid.max <= 0) return null;
  const w = 1 / grid.cols;
  const h = 1 / grid.rows;
  // волна из левого верхнего угла: вся карта проявляется за 400 мс
  const wave = (col: number, row: number) => Math.round(((col + row) / Math.max(1, grid.cols + grid.rows - 2)) * 400);
  return (
    <g className="heat-cells" pointerEvents="none">
      {grid.cells.map((v, i) => {
        const k = v / grid.max;
        if (k < 0.05) return null;
        const hue = 60 - k * 60; // 60 (жёлтый) → 0 (красный)
        const col = i % grid.cols;
        const row = Math.floor(i / grid.cols);
        return (
          <rect
            key={i}
            className="heat-cell"
            style={{ animationDelay: `${wave(col, row)}ms` }}
            x={col * w}
            y={row * h}
            width={w}
            height={h}
            fill={`hsl(${hue} 100% 50%)`}
            opacity={0.15 + k * 0.65}
          />
        );
      })}
    </g>
  );
}
