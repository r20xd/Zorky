"""Перелезание через ограждение: ноги в зоне kind=fence или шаг через её границу N кадров подряд."""

from __future__ import annotations

from ._temporal import TemporalRule
from .base import Verdict, register
from .geometry import Point, foot_point, point_in_polygon


def _cross(o: Point, a: Point, b: Point) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def segments_intersect(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
    d1, d2 = _cross(q1, q2, p1), _cross(q1, q2, p2)
    d3, d4 = _cross(p1, p2, q1), _cross(p1, p2, q2)
    return d1 * d2 < 0 and d3 * d4 < 0


def crosses_border(a: Point, b: Point, polygon: list[Point]) -> bool:
    n = len(polygon)
    return any(segments_intersect(a, b, polygon[i], polygon[(i + 1) % n]) for i in range(n))


@register
class FenceCrossing(TemporalRule):
    name = "fence_crossing"

    def __init__(self, zone_kind: str = "fence", min_frames: int = 3, max_gap_s: float = 2.0):
        """Кадр «попадание»: точка ног внутри зоны вида zone_kind или отрезок от прошлой
        точки ног до текущей пересёк границу зоны. min_frames попаданий подряд — нарушение."""
        super().__init__(max_gap_s)
        if not isinstance(min_frames, int) or min_frames < 1:
            raise ValueError("min_frames должен быть целым >= 1")
        self.zone_kind = zone_kind
        self.min_frames = min_frames
        self.frame = 0
        self._streak: dict[int, tuple[int, int]] = {}  # трек -> (кадр, подряд)
        self._prev: dict[int, tuple[int, Point]] = {}  # трек -> (кадр, ноги)

    def on_frame(self, now: float) -> None:
        if self.now is not None and now < self.now:
            self._streak.clear()
            self._prev.clear()
        super().on_frame(now)
        self.frame += 1

    def check(self, person, detections, zones):
        if self.observe(person) is None:
            return Verdict(False, severity="high")
        foot = foot_point(person.bbox)
        prev = self._prev.get(person.track_id)
        self._prev[person.track_id] = (self.frame, foot)
        last = prev[1] if prev and prev[0] == self.frame - 1 else None
        hit = None
        for zone in zones:
            if zone.get("kind") != self.zone_kind:
                continue
            polygon = [tuple(p) for p in zone["polygon"]]
            if point_in_polygon(foot, polygon) or (
                last is not None and crosses_border(last, foot, polygon)
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
