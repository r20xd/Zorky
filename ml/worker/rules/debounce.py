"""Антидребезг: одно событие на эпизод нарушения у пары (трек, тип)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ..config import CLEAR_S, DEBOUNCE_MIN_FRAMES, DEBOUNCE_WINDOW_S


@dataclass
class _Episode:
    hits: deque = field(default_factory=deque)  # времена кадров с нарушением в окне
    last_active: float = float("-inf")
    fired: bool = False
    last_sent: float = float("-inf")


@dataclass
class Debouncer:
    """Одно событие на эпизод нарушения у пары (трек, тип);
    правила зон передают вместо трека id зоны.

    Нарушение подтверждается min_frames кадрами внутри окна window_s секунд (не обязательно
    подряд). После события пара молчит, пока нарушения нет clear_s секунд подряд (кадры без
    нарушения и пропажа трека считаются одинаково); потом новый эпизод — новое событие.
    repeat_s — пока эпизод длится, раз в столько секунд ещё одно подтверждение (бэкенд склеивает
    его с инцидентом и продлевает окно); None — без подтверждений (оценка на роликах считает
    эпизоды).
    """

    min_frames: int = DEBOUNCE_MIN_FRAMES
    window_s: float = DEBOUNCE_WINDOW_S
    clear_s: float = CLEAR_S
    repeat_s: float | None = None
    _state: dict[tuple[int | str, str], _Episode] = field(default_factory=dict, repr=False)

    def update(self, track_id: int | str, violation: str, active: bool, now: float) -> bool:
        """Учесть кадр; True — пора отправить событие."""
        self._forget_stale(now)
        key = (track_id, violation)
        ep = self._state.get(key)
        if not active:
            return False
        if ep is None or now - ep.last_active >= self.clear_s:
            ep = self._state[key] = _Episode()  # прошлый эпизод закончился
        ep.last_active = now
        ep.hits.append(now)
        while ep.hits and now - ep.hits[0] > self.window_s:
            ep.hits.popleft()
        if len(ep.hits) < self.min_frames:
            return False
        if ep.fired and (self.repeat_s is None or now - ep.last_sent < self.repeat_s):
            return False
        ep.fired = True
        ep.last_sent = now
        return True

    def _forget_stale(self, now: float) -> None:
        """Выкинуть закончившиеся эпизоды, чтобы память не росла за долгую смену."""
        if len(self._state) < 256:
            return
        for key in [k for k, e in self._state.items() if now - e.last_active >= self.clear_s]:
            del self._state[key]
