"""Человек в зоне высоты дольше dwell_s: напоминание мастеру проверить страховку."""

from __future__ import annotations

from ._temporal import TemporalRule, positive
from .base import Verdict, register
from .geometry import foot_point, zone_hit


@register
class AtHeight(TemporalRule):
    name = "at_height"

    def __init__(self, zone_kind: str = "height", dwell_s: float = 3.0, max_gap_s: float = 1.0):
        super().__init__(max_gap_s)
        self.zone_kind = zone_kind
        self.dwell_s = positive(dwell_s, "dwell_s")

    def check(self, person, detections, zones):
        if self.observe(person) is None:
            return Verdict(False)
        zone = zone_hit(foot_point(person.bbox), zones, self.zone_kind)
        if zone is None:
            self.state.pop(person.track_id, None)
            return Verdict(False)
        entry = self.state.get(person.track_id)
        if entry is None or entry[0] != zone["id"]:
            entry = self.state[person.track_id] = (zone["id"], self.now)
        return Verdict(self.now - entry[1] >= self.dwell_s, zone["id"])
