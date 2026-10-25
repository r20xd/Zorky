import { describe, expect, it } from "vitest";
import {
  ARRIVAL_LIMIT,
  FIRST_LOAD_GRACE_MS,
  easeOut,
  newArrivals,
  presenceAfter,
  staggerDelay,
  trackArrivals,
  tweenStep,
} from "./motion";

describe("easeOut и tweenStep", () => {
  it("концы на месте, середина впереди линейной", () => {
    expect(easeOut(0)).toBe(0);
    expect(easeOut(1)).toBe(1);
    expect(easeOut(0.5)).toBeGreaterThan(0.5);
    expect(easeOut(-1)).toBe(0);
    expect(easeOut(2)).toBe(1);
  });
  it("счётчик целый и доходит до цели в обе стороны", () => {
    expect(tweenStep(0, 10, 0)).toBe(0);
    expect(tweenStep(0, 10, 1)).toBe(10);
    expect(Number.isInteger(tweenStep(3, 17, 0.37))).toBe(true);
    expect(tweenStep(10, 2, 1)).toBe(2);
    expect(tweenStep(0, 1.5, 1)).toBe(1.5);
  });
});

describe("staggerDelay", () => {
  it("растёт ступенькой и упирается в потолок", () => {
    expect(staggerDelay(0, 20)).toBe(0);
    expect(staggerDelay(3, 20)).toBe(60);
    expect(staggerDelay(10, 20)).toBe(200);
    expect(staggerDelay(500, 20)).toBe(200);
    expect(staggerDelay(-2, 20)).toBe(0);
  });
});

describe("trackArrivals", () => {
  it("первая загрузка не анимируется, следующие события — да", () => {
    const s = newArrivals(0);
    trackArrivals(s, [], 10); // ещё грузится
    expect(s.base).toBeNull();
    trackArrivals(s, ["a", "b"], 200);
    expect(s.modes.size).toBe(0);
    trackArrivals(s, ["c", "a", "b"], 5000);
    expect(s.modes.get("c")).toBe("shift");
    expect(s.modes.has("a")).toBe(false);
  });
  it("пустой список дольше паузы — это «пусто», первое событие анимируется", () => {
    const s = newArrivals(0);
    trackArrivals(s, [], FIRST_LOAD_GRACE_MS + 1);
    trackArrivals(s, ["a"], FIRST_LOAD_GRACE_MS + 100);
    expect(s.modes.get("a")).toBe("shift");
  });
  it("частые события — без сдвига", () => {
    const s = newArrivals(0);
    trackArrivals(s, ["a"], 100);
    trackArrivals(s, ["b", "a"], 3000);
    trackArrivals(s, ["c", "b", "a"], 3200);
    expect(s.modes.get("b")).toBe("shift");
    expect(s.modes.get("c")).toBe("fade");
  });
  it("за раз анимируются не больше ARRIVAL_LIMIT, остальные — молча", () => {
    const s = newArrivals(0);
    trackArrivals(s, ["x"], 100);
    const many = Array.from({ length: 30 }, (_, i) => `n${i}`);
    trackArrivals(s, [...many, "x"], 5000);
    expect(s.modes.size).toBe(ARRIVAL_LIMIT);
    expect(s.modes.has("n0")).toBe(true);
    expect(s.modes.has("n29")).toBe(false);
    expect(s.base!.has("n29")).toBe(true);
  });
  it("повторный проход по тем же ключам ничего не меняет (StrictMode рисует дважды)", () => {
    const s = newArrivals(0);
    trackArrivals(s, ["a"], 100);
    trackArrivals(s, ["b", "a"], 3000);
    trackArrivals(s, ["b", "a"], 3001);
    expect(s.modes.get("b")).toBe("shift");
    expect(s.lastAt).toBe(3000);
  });
});

describe("presenceAfter", () => {
  it("открыто → закрывается → ушло", () => {
    expect(presenceAfter(true, "gone", 200)).toBe("open");
    expect(presenceAfter(false, "open", 200)).toBe("closing");
    expect(presenceAfter(false, "gone", 200)).toBe("gone");
  });
  it("без анимации (reduced motion) уходит сразу", () => {
    expect(presenceAfter(false, "open", 0)).toBe("gone");
  });
  it("открыли снова во время ухода — снова открыто", () => {
    expect(presenceAfter(true, "closing", 200)).toBe("open");
  });
});
