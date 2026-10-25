"""Время и очистка истории временных правил; источник времени — pipeline.now."""

from __future__ import annotations

import math

from .base import Detection, Rule


def positive(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} должен быть конечным числом > 0")
    return value


def shape(person: Detection) -> tuple[float, tuple[float, float], float] | None:
    x1, y1, x2, y2 = person.bbox
    width, height = x2 - x1, y2 - y1
    if person.track_id < 0 or width <= 0 or height <= 0:
        return None
    if not all(math.isfinite(v) for v in person.bbox):
        return None
    return width / height, ((x1 + x2) / 2, (y1 + y2) / 2), max(width, height)


class TemporalRule(Rule):
    def __init__(self, max_gap_s: float) -> None:
        self.max_gap_s = positive(max_gap_s, "max_gap_s")
        self.now: float | None = None
        self.last_seen: dict[int, float] = {}
        self.state: dict[int, object] = {}

    def on_frame(self, now: float) -> None:
        if not math.isfinite(now):
            raise ValueError("время кадра должно быть конечным")
        if self.now is not None and now < self.now:
            self.last_seen.clear()
            self.state.clear()
        self.now = now
        for track_id, seen in list(self.last_seen.items()):
            if now - seen > self.max_gap_s:
                self.last_seen.pop(track_id)
                self.state.pop(track_id, None)

    def observe(self, person: Detection):
        if self.now is None:
            raise RuntimeError("временное правило требует on_frame(now) перед check")
        geometry = shape(person)
        if geometry is not None:
            self.last_seen[person.track_id] = self.now
        return geometry
