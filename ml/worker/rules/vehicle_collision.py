"""Встречный курс техники: время до столкновения по скорости сближения рамок меньше ttc_s."""

from __future__ import annotations

from .base import Detection, ZoneRule, ZoneVerdict, register
from .vehicle_proximity import VEHICLE_CLASSES, bbox_gap


def _union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


@register
class VehicleCollision(ZoneRule):
    """Зон не нужно: правило смотрит пары треков техники. Событие без zone_id, критичное."""

    name = "vehicle_collision"
    wants_detections = True

    def __init__(
        self,
        ttc_s: float = 3.0,
        min_closing: float = 0.05,
        min_frames: int = 3,
        vehicle_classes=VEHICLE_CLASSES,
    ) -> None:
        """min_closing — скорость сближения (высот кадра в с), ниже — техника «стоит»."""
        if ttc_s <= 0 or min_closing <= 0:
            raise ValueError("ttc_s и min_closing должны быть > 0")
        if not isinstance(min_frames, int) or min_frames < 1:
            raise ValueError("min_frames должен быть целым >= 1")
        self.ttc_s, self.min_closing, self.min_frames = ttc_s, min_closing, min_frames
        self.vehicle_classes = frozenset(vehicle_classes)
        self.now = 0.0
        self.frame = 0
        self._prev: dict[tuple[int, int], tuple[int, float, float]] = {}  # кадр, время, зазор
        self._streak: dict[tuple[int, int], tuple[int, int]] = {}  # кадр, подряд

    def on_frame(self, now: float) -> None:
        if now < self.now:  # новое видео
            self._prev.clear()
            self._streak.clear()
        self.now = now
        self.frame += 1

    def check_frame(self, detections, zones):
        vehicles = sorted(
            (d for d in detections if d.cls in self.vehicle_classes and d.track_id >= 0),
            key=lambda d: d.track_id,
        )
        out, seen = [], set()
        for i, a in enumerate(vehicles):
            for b in vehicles[i + 1 :]:
                if a.track_id == b.track_id:
                    continue
                key = (a.track_id, b.track_id)
                seen.add(key)
                gap = bbox_gap(a.bbox, b.bbox, a.frame_aspect)
                active = self._colliding(key, gap)
                pframe, count = self._streak.get(key, (-2, 0))
                count = count + 1 if active and pframe == self.frame - 1 else (1 if active else 0)
                if active:
                    self._streak[key] = (self.frame, count)
                else:
                    self._streak.pop(key, None)
                box = Detection(
                    a.cls,
                    _union(a.bbox, b.bbox),
                    min(a.confidence, b.confidence),
                    a.track_id,
                    a.frame_aspect,
                )
                out.append(
                    ZoneVerdict(
                        {"key": f"{a.track_id}-{b.track_id}"},
                        count >= self.min_frames,
                        "critical",
                        box,
                    )
                )
        for table in (self._prev, self._streak):
            for key in [k for k in table if k not in seen]:
                del table[key]
        return out

    def _colliding(self, key, gap: float) -> bool:
        prev = self._prev.get(key)
        self._prev[key] = (self.frame, self.now, gap)
        if prev is None or prev[0] != self.frame - 1 or self.now <= prev[1]:
            return False
        closing = (prev[2] - gap) / (self.now - prev[1])
        if closing < self.min_closing:
            return False
        return gap / closing < self.ttc_s
