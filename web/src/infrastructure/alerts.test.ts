import { afterEach, describe, expect, it, vi } from "vitest";
import { alertsEnabled, setAlertsEnabled, vibrate } from "./alerts";

function fakeStorage() {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
  };
}

describe("alerts", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("по умолчанию включены, выключатель помнится", () => {
    vi.stubGlobal("localStorage", fakeStorage());
    vi.stubGlobal("window", { dispatchEvent: vi.fn() });
    expect(alertsEnabled()).toBe(true);
    setAlertsEnabled(false);
    expect(alertsEnabled()).toBe(false);
    setAlertsEnabled(true);
    expect(alertsEnabled()).toBe(true);
  });

  it("выключено — не вибрирует", () => {
    const buzz = vi.fn();
    vi.stubGlobal("localStorage", fakeStorage());
    vi.stubGlobal("window", { dispatchEvent: vi.fn() });
    vi.stubGlobal("navigator", { vibrate: buzz });
    vibrate([100]);
    expect(buzz).toHaveBeenCalledTimes(1);
    setAlertsEnabled(false);
    vibrate([100]);
    expect(buzz).toHaveBeenCalledTimes(1);
  });

  it("хранилище недоступно — оповещения остаются включёнными", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("blocked");
      },
    });
    expect(alertsEnabled()).toBe(true);
  });
});
