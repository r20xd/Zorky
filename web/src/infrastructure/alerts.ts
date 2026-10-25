/** Звук и вибрация оповещений: один выключатель на браузер, помнится между запусками. */
const KEY = "mosprom.alerts";
const CHANGED = "mosprom-alerts-changed";

export function alertsEnabled(): boolean {
  try {
    return localStorage.getItem(KEY) !== "off";
  } catch {
    return true; // хранилище недоступно (приватное окно) — оповещения по умолчанию включены
  }
}

export function setAlertsEnabled(on: boolean): void {
  try {
    localStorage.setItem(KEY, on ? "on" : "off");
  } catch {
    // не запомнится — работает до перезагрузки
  }
  window.dispatchEvent(new Event(CHANGED));
}

/** Для useSyncExternalStore: подписка на смену настройки в этой и в соседних вкладках. */
export function subscribeAlerts(onChange: () => void): () => void {
  window.addEventListener(CHANGED, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(CHANGED, onChange);
    window.removeEventListener("storage", onChange);
  };
}

/** Вибрация, если оповещения включены и телефон умеет. */
export function vibrate(pattern: number[]): void {
  if (!alertsEnabled()) return;
  try {
    navigator.vibrate?.(pattern);
  } catch {
    // не поддерживается
  }
}
