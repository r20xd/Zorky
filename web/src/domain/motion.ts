// Чистая логика анимаций: кривые, задержки, какие элементы списка «новые», жизнь окна при закрытии.

/** ease-out (кубическая): быстро в начале, мягко в конце. t — 0..1. */
export const easeOut = (t: number): number => 1 - Math.pow(1 - clamp01(t), 3);

const clamp01 = (t: number) => (t < 0 ? 0 : t > 1 ? 1 : t);

/** Промежуточное значение счётчика на шаге t (0..1): целые остаются целыми. */
export function tweenStep(from: number, to: number, t: number): number {
  const value = from + (to - from) * easeOut(t);
  return Number.isInteger(from) && Number.isInteger(to) ? Math.round(value) : value;
}

/** Задержка появления i-го элемента: ступенька step, после cap элементов — без роста. */
export const staggerDelay = (i: number, stepMs: number, cap = 10): number => Math.min(Math.max(i, 0), cap) * stepMs;

/** Не больше стольких новых элементов за раз получают анимацию появления. */
export const ARRIVAL_LIMIT = 20;
/** События чаще этого — без сдвига, только проявление: лента не прыгает. */
export const ARRIVAL_BURST_MS = 400;
/** Пустой список в первые мс после показа — ещё загрузка, а не «пусто». */
export const FIRST_LOAD_GRACE_MS = 1500;

export type Arrival = "shift" | "fade";

export interface ArrivalState {
  /** Ключи, что были при первой загрузке, — их не анимируем. null — загрузки ещё не было. */
  base: Set<string> | null;
  startedAt: number;
  lastAt: number;
  modes: Map<string, Arrival>;
}

export const newArrivals = (now: number): ArrivalState => ({
  base: null,
  startedAt: now,
  lastAt: -Infinity,
  modes: new Map(),
});

/**
 * Отмечает новые ключи списка: первая загрузка не анимируется, дальше — не больше ARRIVAL_LIMIT
 * за раз (порядок списка: сверху — свежие), частые поступления — без сдвига. Меняет state.
 */
export function trackArrivals(state: ArrivalState, keys: string[], now: number): ArrivalState {
  if (state.base === null) {
    if (keys.length > 0 || now - state.startedAt > FIRST_LOAD_GRACE_MS) state.base = new Set(keys);
    return state;
  }
  const fresh = keys.filter((k) => !state.base!.has(k) && !state.modes.has(k));
  if (fresh.length === 0) return state;
  const mode: Arrival = now - state.lastAt < ARRIVAL_BURST_MS ? "fade" : "shift";
  fresh.forEach((k, i) => (i < ARRIVAL_LIMIT ? state.modes.set(k, mode) : state.base!.add(k)));
  state.lastAt = now;
  return state;
}

/** Окно (лайтбокс, карточка события): после закрытия ещё живёт, пока играет уход. */
export type Presence = "open" | "closing" | "gone";

export function presenceAfter(open: boolean, previous: Presence, durationMs: number): Presence {
  if (open) return "open";
  if (previous === "gone" || durationMs <= 0) return "gone";
  return "closing";
}
