"""Падение: быстрая смена вертикального bbox на горизонтальный."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ._temporal import TemporalRule, positive
from .base import Verdict, register

HEAD_POINTS = (0, 1, 2, 3, 4)  # COCO-17: нос, глаза, уши
HIP_POINTS = (11, 12)


def lying_by_pose(keypoints, max_slope: float = 0.5) -> bool | None:
    """Лежит ли человек по позе COCO-17: голова на уровне бёдер.

    |dy| между центром головы и центром бёдер не больше max_slope от расстояния между ними
    (при 0.5 туловище отклонено от горизонтали меньше 30 градусов). Не зависит от рамки:
    с высокой камеры
    рамка лежащего может быть вертикальной. None - точек головы или бёдер нет (conf <= 0.3)."""
    if not keypoints or len(keypoints) < 17:
        return None

    def centre(indices):
        pts = [keypoints[i] for i in indices if keypoints[i][2] > 0.3]
        if not pts:
            return None
        return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)

    head, hip = centre(HEAD_POINTS), centre(HIP_POINTS)
    if head is None or hip is None:
        return None
    dist = math.hypot(head[0] - hip[0], head[1] - hip[1])
    if dist <= 0:
        return None
    return abs(head[1] - hip[1]) <= max_slope * dist


@dataclass
class _Fall:
    upright_at: float | None = None
    upright_ratio: float = 1.0
    fallen: bool = False


@register
class Fall(TemporalRule):
    name = "fall"

    def __init__(
        self,
        upright_ratio=0.8,
        lying_ratio=1.2,
        min_ratio_change=2.0,
        transition_s=1.0,
        max_gap_s=2.0,
        pose_lying=False,
        pose_max_slope=0.5,
    ):
        super().__init__(max_gap_s)
        self.upright_ratio = positive(upright_ratio, "upright_ratio")
        self.lying_ratio = positive(lying_ratio, "lying_ratio")
        self.min_ratio_change = positive(min_ratio_change, "min_ratio_change")
        self.transition_s = positive(transition_s, "transition_s")
        self.pose_lying = bool(
            pose_lying
        )  # проверка лежания по позе, когда точки есть; по умолчанию выкл.
        self.pose_max_slope = positive(pose_max_slope, "pose_max_slope")
        if self.upright_ratio >= self.lying_ratio:
            raise ValueError("upright_ratio должен быть меньше lying_ratio")

    def check(self, person, detections, zones):
        geometry = self.observe(person)
        if geometry is None:
            return Verdict(False)
        ratio, _, _ = geometry
        state = self.state.setdefault(person.track_id, _Fall())
        pose = lying_by_pose(person.keypoints, self.pose_max_slope) if self.pose_lying else None
        if pose and state.upright_at is not None:
            # лежит по позе (рамка может быть вертикальной): смена формы рамки не нужна
            if self.now - state.upright_at <= self.transition_s:
                state.fallen = True
        elif pose:
            pass
        elif ratio <= self.upright_ratio:
            state.upright_at, state.upright_ratio, state.fallen = self.now, ratio, False
        elif ratio >= self.lying_ratio and state.upright_at is not None:
            if (
                self.now - state.upright_at <= self.transition_s
                and ratio / state.upright_ratio >= self.min_ratio_change
            ):
                state.fallen = True
        # Сохраняем активность всего лежачего эпизода, а не единственный кадр перехода.
        return Verdict(state.fallen, severity="critical")
