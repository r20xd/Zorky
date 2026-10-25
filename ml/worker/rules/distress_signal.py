"""Сигнал бедствия: оба запястья выше носа и глаз дольше hold_s у одного трека (поза COCO-17)."""

from __future__ import annotations

from ._temporal import TemporalRule, positive
from .base import Verdict, register

NOSE, L_EYE, R_EYE, L_WRIST, R_WRIST = 0, 1, 2, 9, 10


@register
class DistressSignal(TemporalRule):
    name = "distress_signal"

    def __init__(self, hold_s: float = 5.0, min_confidence: float = 0.3, max_gap_s: float = 0.5):
        """hold_s — сколько секунд подряд обе руки подняты. Пропал кадр без позы дольше
        max_gap_s или руки опустились — отсчёт заново (мах одной рукой не считается)."""
        super().__init__(max_gap_s)
        self.hold_s = positive(hold_s, "hold_s")
        self.min_confidence = float(min_confidence)

    def _hands_up(self, kp) -> bool:
        if kp is None or len(kp) <= R_WRIST:
            return False
        head = [kp[i] for i in (NOSE, L_EYE, R_EYE)]
        wrists = [kp[L_WRIST], kp[R_WRIST]]
        if any(p[2] < self.min_confidence for p in head[:1] + wrists):
            return False
        top = min(p[1] for p in head if p[2] >= self.min_confidence)  # y растёт вниз
        return all(w[1] < top for w in wrists)

    def check(self, person, detections, zones):
        if self.observe(person) is None:
            return Verdict(False)
        if not self._hands_up(person.keypoints):
            self.state.pop(person.track_id, None)
            return Verdict(False, severity="critical")
        start = self.state.setdefault(person.track_id, self.now)
        return Verdict(self.now - start >= self.hold_s, severity="critical")
