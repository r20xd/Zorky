"""Опасное сближение человека с техникой: зазор между рамками в ростах человека."""

from __future__ import annotations

import logging
import math

from ._temporal import TemporalRule, positive
from .base import Detection, Verdict, register

log = logging.getLogger("worker.vehicle_proximity")

VEHICLE_CLASSES = ("car", "truck", "bus", "train", "forklift")


def bbox_gap(a, b, aspect: float = 1.0) -> float:
    """Расстояние между рамками (0 при пересечении) в единицах высоты кадра."""
    dx = max(b[0] - a[2], a[0] - b[2], 0.0) * aspect
    dy = max(b[1] - a[3], a[1] - b[3], 0.0)
    return math.hypot(dx, dy)


@register
class VehicleProximity(TemporalRule):
    name = "vehicle_proximity"

    def __init__(
        self,
        gap_static=0.3,
        gap_moving=0.8,
        move_speed=0.15,
        min_frames=5,
        max_gap_s=2.0,
        vehicle_classes=VEHICLE_CLASSES,
    ):
        """gap_* — зазор в ростах человека: у стоящей техники gap_static, у движущейся
        (центр смещается быстрее move_speed высот техники в секунду) строже — gap_moving."""
        super().__init__(max_gap_s)
        self.gap_static = positive(gap_static, "gap_static")
        self.gap_moving = positive(gap_moving, "gap_moving")
        self.move_speed = positive(move_speed, "move_speed")
        if not isinstance(min_frames, int) or min_frames < 1:
            raise ValueError("min_frames должен быть целым >= 1")
        self.min_frames = min_frames
        self.vehicle_classes = frozenset(vehicle_classes)
        self.frame = 0
        self._motion: dict[int, tuple[float, float, float, bool]] = {}  # трек -> t, cx, cy, moving
        self._streak: dict[int, tuple[int, int]] = {}  # трек человека -> (кадр, подряд)

    def on_frame(self, now: float) -> None:
        if self.now is not None and now < self.now:
            self._motion.clear()
            self._streak.clear()
        super().on_frame(now)
        self.frame += 1

    def _moving(self, vehicle: Detection) -> bool:
        if vehicle.track_id < 0:
            return False
        x1, y1, x2, y2 = vehicle.bbox
        cx, cy = (x1 + x2) / 2 * vehicle.frame_aspect, (y1 + y2) / 2
        size = max((x2 - x1) * vehicle.frame_aspect, y2 - y1)
        prev = self._motion.get(vehicle.track_id)
        if prev is None or self.now - prev[0] > self.max_gap_s:
            self._motion[vehicle.track_id] = (self.now, cx, cy, False)
            return False
        t, px, py, moving = prev
        dt = self.now - t
        if dt <= 0:
            return moving
        # Смещение копится от якоря, чтобы медленный ход не терялся между кадрами.
        speed = math.hypot(cx - px, cy - py) / size / dt
        if speed > self.move_speed:
            self._motion[vehicle.track_id] = (self.now, cx, cy, True)
            return True
        if dt >= 0.5:
            self._motion[vehicle.track_id] = (self.now, cx, cy, False)
            return False
        return moving

    def check(self, person, detections, zones):
        if self.observe(person) is None:
            return Verdict(False, severity="critical")
        worst = None  # (запас, зазор, техника)
        for vehicle in detections:
            if vehicle.cls not in self.vehicle_classes:
                continue
            moving = self._moving(vehicle)
            limit = self.gap_moving if moving else self.gap_static
            height = person.bbox[3] - person.bbox[1]
            gap = bbox_gap(person.bbox, vehicle.bbox, person.frame_aspect) / height
            if gap < limit and (worst is None or gap - limit < worst[0]):
                worst = (gap - limit, gap, vehicle, moving)
        frame, count = self._streak.get(person.track_id, (-2, 0))
        if worst is None:
            self._streak.pop(person.track_id, None)
            return Verdict(False, severity="critical")
        count = count + 1 if frame == self.frame - 1 else 1
        self._streak[person.track_id] = (self.frame, count)
        active = count >= self.min_frames
        if active and count == self.min_frames:
            _, gap, vehicle, moving = worst
            log.info(
                "человек %d: техника %s (трек %d, %s), зазор %.2f роста",
                person.track_id,
                vehicle.cls,
                vehicle.track_id,
                "движется" if moving else "стоит",
                gap,
            )
        return Verdict(active, severity="critical")
