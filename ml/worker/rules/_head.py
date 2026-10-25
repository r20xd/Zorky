from __future__ import annotations

from .base import Detection, Rule, Verdict
from .geometry import HEAD_PART, in_head_area


class HeadRule(Rule):
    """Основа правил «в области головы есть X, а Y нет». Параметры: head_part, min_confidence."""

    def __init__(self, head_part: float = HEAD_PART, min_confidence: float = 0.0) -> None:
        self.head_part = head_part
        self.min_confidence = min_confidence

    def near(self, person: Detection, detections: list[Detection]) -> set[str]:
        return {
            d.cls
            for d in detections
            if d.confidence >= self.min_confidence
            and in_head_area(person.bbox, d.bbox, self.head_part)
        }

    def check(self, person: Detection, detections: list[Detection], zones: list[dict]) -> Verdict:
        return Verdict(self.violated(self.near(person, detections)))

    def violated(self, near: set[str]) -> bool:
        raise NotImplementedError
