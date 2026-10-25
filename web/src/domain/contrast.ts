/** Цвета баннера временного пароля (тёмная тема) — те же значения в styles.css (.temp-password). */
export const TEMP_PASSWORD_COLORS = { text: "#f6ecc9", bg: "#2e2711", password: "#ffe27a" } as const;

/** Порог WCAG AA для обычного текста. */
export const WCAG_AA = 4.5;

function channel(v: number): number {
  const s = v / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

/** Относительная яркость цвета «#rrggbb» (WCAG 2.x). */
export function luminance(hex: string): number {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m) throw new Error(`Ожидался цвет #rrggbb: ${hex}`);
  const n = parseInt(m[1], 16);
  return 0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
}

/** Контраст двух цветов «#rrggbb», от 1 до 21. */
export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
