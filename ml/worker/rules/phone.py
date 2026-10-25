"""Телефон у головы или кисти человека (COCO `cell phone`) N кадров подряд."""

from __future__ import annotations

from ._temporal import TemporalRule, positive
from .base import Verdict, register
from .geometry import foot_point, zone_hit


@register
class Phone(TemporalRule):
    name = "phone"

    def __init__(
        self,
        zone_kind: str | None = None,
        min_frames: int = 5,
        head_part: float = 0.5,
        side_margin: float = 0.25,
        min_confidence: float = 0.0,
        max_gap_s: float = 2.0,
    ):
        """zone_kind — вид зоны (например machinery), где телефон запрещён; None — везде.
        «Рядом с головой или кистью» по рамке: центр телефона в верхней доле head_part рамки
        человека, по бокам шире рамки на side_margin ширины (рука с телефоном у уха)."""
        super().__init__(max_gap_s)
        if not isinstance(min_frames, int) or min_frames < 1:
            raise ValueError("min_frames должен быть целым >= 1")
        self.zone_kind = zone_kind
        self.min_frames = min_frames
        self.head_part = positive(head_part, "head_part")
        self.side_margin = float(side_margin)
        self.min_confidence = float(min_confidence)
        self.frame = 0
        self._streak: dict[int, tuple[int, int]] = {}  # трек -> (кадр, подряд)

    def on_frame(self, now: float) -> None:
        if self.now is not None and now < self.now:
            self._streak.clear()
        super().on_frame(now)
        self.frame += 1

    def _near(self, person, item) -> bool:
        x1, y1, x2, y2 = person.bbox
        cx, cy = (item.bbox[0] + item.bbox[2]) / 2, (item.bbox[1] + item.bbox[3]) / 2
        margin = (x2 - x1) * self.side_margin
        return x1 - margin <= cx <= x2 + margin and y1 <= cy <= y1 + (y2 - y1) * self.head_part

    def check(self, person, detections, zones):
        if self.observe(person) is None:
            return Verdict(False)
        zone_id = None
        if self.zone_kind is not None:
            zone = zone_hit(foot_point(person.bbox), zones, self.zone_kind)
            if zone is None:
                self._streak.pop(person.track_id, None)
                return Verdict(False)
            zone_id = zone["id"]
        has = any(
            d.cls == "cell phone" and d.confidence >= self.min_confidence and self._near(person, d)
            for d in detections
        )
        if not has:
            self._streak.pop(person.track_id, None)
            return Verdict(False, zone_id)
        frame, count = self._streak.get(person.track_id, (-2, 0))
        count = count + 1 if frame == self.frame - 1 else 1
        self._streak[person.track_id] = (self.frame, count)
        return Verdict(count >= self.min_frames, zone_id)
