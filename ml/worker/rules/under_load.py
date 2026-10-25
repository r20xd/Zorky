"""Подлезание под конвейер или груз: ноги в зоне kind=under_load и человек пригнулся или ползёт."""

from __future__ import annotations

import statistics
from collections import deque

from ._temporal import TemporalRule, positive
from .base import Verdict, register
from .geometry import foot_point, point_in_polygon

BASELINE_FRAMES = 60


@register
class UnderLoad(TemporalRule):
    name = "under_load"

    def __init__(
        self,
        zone_kind: str = "under_load",
        height_frac: float = 0.6,
        min_aspect: float = 1.0,
        min_frames: int = 5,
        max_gap_s: float = 2.0,
    ):
        """Кадр «попадание»: точка ног в зоне zone_kind и пригнулся: высота рамки меньше
        height_frac медианной высоты этого трека стоя или высота/ширина (в пикселях) ниже
        min_aspect. Высота стоя — медиана последних кадров трека без пригибания.
        min_frames попаданий подряд — нарушение."""
        super().__init__(max_gap_s)
        if not isinstance(min_frames, int) or min_frames < 1:
            raise ValueError("min_frames должен быть целым >= 1")
        self.zone_kind = zone_kind
        self.height_frac = positive(height_frac, "height_frac")
        self.min_aspect = positive(min_aspect, "min_aspect")
        self.min_frames = min_frames
        self.frame = 0
        self._streak: dict[int, tuple[int, int]] = {}  # трек -> (кадр, подряд)
        self._standing: dict[int, deque] = {}  # трек -> высоты стоя

    def on_frame(self, now: float) -> None:
        if self.now is not None and now < self.now:
            self._streak.clear()
            self._standing.clear()
        super().on_frame(now)
        self.frame += 1
        for track_id in list(self._standing):
            if track_id not in self.last_seen:
                self._standing.pop(track_id)
                self._streak.pop(track_id, None)

    def _crouched(self, person) -> bool:
        x1, y1, x2, y2 = person.bbox
        height, width = y2 - y1, (x2 - x1) * person.frame_aspect
        low_aspect = height / width < self.min_aspect
        heights = self._standing.setdefault(person.track_id, deque(maxlen=BASELINE_FRAMES))
        low_height = bool(heights) and height < self.height_frac * statistics.median(heights)
        crouched = low_aspect or low_height
        if not crouched:
            heights.append(height)
        return crouched

    def check(self, person, detections, zones):
        if self.observe(person) is None:
            return Verdict(False, severity="high")
        crouched = self._crouched(person)
        foot = foot_point(person.bbox)
        hit = None
        if crouched:
            for zone in zones:
                if zone.get("kind") == self.zone_kind and point_in_polygon(
                    foot, [tuple(p) for p in zone["polygon"]]
                ):
                    hit = zone
                    break
        if hit is None:
            self._streak.pop(person.track_id, None)
            return Verdict(False, severity="high")
        frame, count = self._streak.get(person.track_id, (-2, 0))
        count = count + 1 if frame == self.frame - 1 else 1
        self._streak[person.track_id] = (self.frame, count)
        return Verdict(count >= self.min_frames, hit["id"], "high")
