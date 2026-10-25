"""Техника в запрещённой зоне: нижняя кромка рамки трека техники в зоне danger N кадров подряд."""

from __future__ import annotations

from .base import ZoneRule, ZoneVerdict, register
from .geometry import foot_point, point_in_polygon
from .vehicle_proximity import VEHICLE_CLASSES


@register
class VehicleInZone(ZoneRule):
    name = "vehicle_in_zone"
    wants_detections = True

    def __init__(
        self, zone_kind: str = "danger", min_frames: int = 10, vehicle_classes=VEHICLE_CLASSES
    ) -> None:
        if not isinstance(min_frames, int) or min_frames < 1:
            raise ValueError("min_frames должен быть целым >= 1")
        self.zone_kind = zone_kind
        self.min_frames = min_frames
        self.vehicle_classes = frozenset(vehicle_classes)
        self.now = 0.0
        self.frame = 0
        self._streak: dict[tuple[str, int], tuple[int, int]] = {}  # (зона, трек) -> кадр, подряд
        self._last: dict[tuple[str, int], ZoneVerdict] = {}

    def on_frame(self, now: float) -> None:
        if now < self.now:  # новое видео
            self._streak.clear()
        self.now = now
        self.frame += 1

    def check_frame(self, detections, zones):
        out = []
        seen = set()
        for zone in zones:
            if zone.get("kind") != self.zone_kind:
                continue
            polygon = [tuple(p) for p in zone["polygon"]]
            for d in detections:
                if d.cls not in self.vehicle_classes or d.track_id < 0:
                    continue
                if not point_in_polygon(foot_point(d.bbox), polygon):
                    continue
                key = (str(zone["id"]), d.track_id)
                seen.add(key)
                prev_frame, count = self._streak.get(key, (-2, 0))
                count = count + 1 if prev_frame == self.frame - 1 else 1
                self._streak[key] = (self.frame, count)
                out.append(ZoneVerdict(zone, count >= self.min_frames, "critical", d))
        for key in [k for k in self._streak if k not in seen]:
            if self._streak[key][0] < self.frame:
                del self._streak[key]
        return out
