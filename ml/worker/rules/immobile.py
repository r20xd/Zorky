"""Критичная неподвижность лежащего человека; обычное стояние не считается."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ._temporal import TemporalRule, positive
from .base import Verdict, register


@dataclass
class _Still:
    since: float
    center: tuple[float, float]
    scale: float


@register
class Immobile(TemporalRule):
    name = "immobile"

    def __init__(self, duration_s=10.0, lying_ratio=1.2, max_center_shift=0.1, max_gap_s=2.0):
        super().__init__(max_gap_s)
        self.duration_s = positive(duration_s, "duration_s")
        self.lying_ratio = positive(lying_ratio, "lying_ratio")
        self.max_center_shift = positive(max_center_shift, "max_center_shift")

    def check(self, person, detections, zones):
        geometry = self.observe(person)
        if geometry is None:
            return Verdict(False)
        ratio, center, scale = geometry
        if ratio < self.lying_ratio:
            self.state.pop(person.track_id, None)
            return Verdict(False)
        state = self.state.get(person.track_id)
        if state is None or math.dist(center, state.center) > self.max_center_shift * state.scale:
            state = self.state[person.track_id] = _Still(self.now, center, scale)
        return Verdict(self.now - state.since >= self.duration_s, severity="critical")
