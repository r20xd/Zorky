import { alertsEnabled } from "./alerts";

/** Громкость сигналов: тихо, чтобы не резало слух на показе; звук нужен как напоминание, а не сирена. */
export const BEEP_VOLUME = 0.05;

/** Короткий тон с плавным началом и затуханием (без щелчков). Звук недоступен — молча пропускаем. */
export function playTone(freq: number, ms: number, volume = BEEP_VOLUME) {
  if (!alertsEnabled()) return;
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    const end = ctx.currentTime + ms / 1000;
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(volume, ctx.currentTime + 0.03);
    gain.gain.exponentialRampToValueAtTime(0.0001, end);
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(end);
    osc.onended = () => void ctx.close();
  } catch {
    // нет жеста пользователя — не страшно
  }
}
