// Тонкие хуки движения: вся логика — в domain/motion.ts (с тестами), здесь только React и таймеры.
import { useEffect, useRef, useState } from "react";
import { newArrivals, presenceAfter, trackArrivals, tweenStep, type Presence } from "../../domain/motion";

const REDUCE = "(prefers-reduced-motion: reduce)";

/** Пользователь просит меньше движения (настройка ОС). */
export function reducedMotion(): boolean {
  try {
    return typeof window !== "undefined" && !!window.matchMedia?.(REDUCE).matches;
  } catch {
    return false;
  }
}

export function useReducedMotion(): boolean {
  const [reduce, setReduce] = useState(reducedMotion);
  useEffect(() => {
    const query = window.matchMedia?.(REDUCE);
    if (!query) return;
    const update = () => setReduce(query.matches);
    query.addEventListener?.("change", update);
    return () => query.removeEventListener?.("change", update);
  }, []);
  return reduce;
}

/** true после первого кадра: то, что было на экране при загрузке, не анимируем. */
export function useMounted(): boolean {
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(id);
  }, []);
  return mounted;
}

/** Число плавно доезжает до нового значения; первое значение и reduced motion — сразу. */
export function useCountUp(value: number, durationMs = 400): number {
  const [shown, setShown] = useState(value);
  const shownRef = useRef(value);
  useEffect(() => {
    const from = shownRef.current;
    if (from === value) return;
    if (reducedMotion() || durationMs <= 0) {
      shownRef.current = value;
      setShown(value);
      return;
    }
    const start = performance.now();
    let frame = 0;
    const step = (now: number) => {
      const t = (now - start) / durationMs;
      const next = tweenStep(from, value, t);
      shownRef.current = next;
      setShown(next);
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [value, durationMs]);
  return shown;
}

/**
 * Окно, которое после закрытия ещё живёт durationMs, пока играет уход.
 * mounted — рисовать ли; state — "open" | "closing" для класса.
 */
export function usePresence(open: boolean, durationMs = 200): { mounted: boolean; state: Presence } {
  const [state, setState] = useState<Presence>(open ? "open" : "gone");
  const duration = reducedMotion() ? 0 : durationMs;
  const next = presenceAfter(open, state, duration);
  if (next !== state && !(state === "closing" && next === "closing")) setState(next);
  useEffect(() => {
    if (state !== "closing") return;
    const id = window.setTimeout(() => setState("gone"), duration);
    return () => window.clearTimeout(id);
  }, [state, duration]);
  return { mounted: next !== "gone", state: next };
}

/**
 * Какие элементы списка — свежие: класс fx-arrive (сдвиг + проявление), fx-arrive-fade (частые — без сдвига)
 * или "". Первая загрузка не анимируется. Класс остаётся на элементе — анимация играет один раз при появлении.
 */
export function useArrivals(keys: string[]): (key: string) => string {
  const ref = useRef<ReturnType<typeof newArrivals> | null>(null);
  if (ref.current === null) ref.current = newArrivals(Date.now());
  const state = trackArrivals(ref.current, keys, Date.now());
  return (key) => {
    const mode = state.modes.get(key);
    return mode === "shift" ? "fx-arrive" : mode === "fade" ? "fx-arrive-fade" : "";
  };
}

/** Фокус вернуть туда, где он был до открытия окна (лайтбокс, карточка события). */
export function useReturnFocus(open: boolean): void {
  const before = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!open) return;
    before.current = document.activeElement as HTMLElement | null;
    return () => {
      const el = before.current;
      if (el && document.contains(el)) el.focus({ preventScroll: true });
    };
  }, [open]);
}

/** Короткая вибрация на телефоне в ответ на действие; без reduced motion и без ошибок. */
export function buzz(ms = 30): void {
  if (reducedMotion()) return;
  try {
    navigator.vibrate?.(ms);
  } catch {
    /* не умеет — и ладно */
  }
}

const preloaded = new Set<string>();
/** Догрузить снимок заранее (по наведению): к клику он уже в кэше браузера. */
export function preload(src: string | null | undefined): void {
  if (!src || preloaded.has(src) || preloaded.size > 200) return;
  preloaded.add(src);
  const img = new Image();
  img.decoding = "async";
  img.src = src;
}

/** Последнее непустое значение: окно, что закрывается, ещё показывает своё содержимое. */
export function useLingering<T>(value: T | null): T | null {
  const [last, setLast] = useState<T | null>(value);
  if (value !== null && value !== last) setLast(value);
  return value ?? last;
}
