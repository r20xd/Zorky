import { useSyncExternalStore } from "react";
import { alertsEnabled, setAlertsEnabled, subscribeAlerts } from "../infrastructure/alerts";

/** Включены ли звук и вибрация в этом браузере + переключатель. */
export function useAlerts(): [boolean, () => void] {
  const on = useSyncExternalStore(subscribeAlerts, alertsEnabled, () => true);
  return [on, () => setAlertsEnabled(!on)];
}
