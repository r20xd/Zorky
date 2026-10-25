"""Бег в зоне техники: скорость центра трека в ростах человека за секунду."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from ._temporal import TemporalRule, positive
from .base import Verdict, register
from .geometry import foot_point, zone_hit


@dataclass
class _Motion:
    zone_id: str
    points: deque = field(default_factory=deque)


@register
class Running(TemporalRule):
    name = "running"

    def __init__(
        self,
        zone_kind="machinery",
        speed_threshold=1.5,
        window_s=0.5,
        min_span_s=0.25,
        min_samples=3,
        max_gap_s=2.0,
    ):
        super().__init__(max_gap_s)
        self.zone_kind = str(zone_kind)
        self.speed_threshold = positive(speed_threshold, "speed_threshold")
        self.window_s = positive(window_s, "window_s")
        self.min_span_s = positive(min_span_s, "min_span_s")
        if self.min_span_s > self.window_s:
            raise ValueError("min_span_s должен быть <= window_s")
        if not isinstance(min_samples, int) or min_samples < 2:
            raise ValueError("min_samples должен быть целым >= 2")
        self.min_samples = min_samples

    def check(self, person, detections, zones):
        geometry = self.observe(person)
        if geometry is None:
            return Verdict(False)
        _, center, _ = geometry
        aspect = positive(person.frame_aspect, "frame_aspect")
        zone = zone_hit(foot_point(person.bbox), zones, self.zone_kind)
        if zone is None:
            self.state.pop(person.track_id, None)
            return Verdict(False)
        state = self.state.get(person.track_id)
        if state is None or state.zone_id != zone["id"]:
            state = self.state[person.track_id] = _Motion(zone["id"])
        # Горизонтальные координаты приводятся к единицам высоты кадра.
        point = (self.now, center[0] * aspect, center[1], person.bbox[3] - person.bbox[1])
        if state.points and self.now == state.points[-1][0]:
            # Повтор одного timestamp не доказывает движение и не увеличивает выборку.
            state.points[-1] = point
            return Verdict(False, zone["id"])
        state.points.append(point)
        # Один опорный кадр до начала окна сохраняет наблюдение при невысоком FPS.
        while len(state.points) > 2 and self.now - state.points[1][0] > self.window_s:
            state.points.popleft()
        start = state.points[0]
        elapsed = self.now - start[0]
        if len(state.points) < self.min_samples or elapsed < self.min_span_s:
            return Verdict(False, zone["id"])
        average_height = sum(p[3] for p in state.points) / len(state.points)
        speed = math.hypot(point[1] - start[1], point[2] - start[2]) / average_height / elapsed
        return Verdict(speed > self.speed_threshold, zone["id"], "low")
