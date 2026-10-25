"""Один человек — одно событие, даже если детектор вёл его двумя треками.

Антидребезг (rules.Debouncer) различает людей по id трека. Когда одного человека ведут два
трека (двойная рамка, смена id), нарушение даёт два события. OverlapDeduper отбрасывает
событие того же типа, если его рамка перекрывает (IoU) рамку события, отправленного
в последние window_s секунд. Нарушения зон (без трека) не трогает.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def iou(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


@dataclass
class OverlapDeduper:
    min_iou: float = 0.5
    window_s: float = 10.0
    _sent: list[tuple[str, tuple, float]] = field(default_factory=list, repr=False)

    def allow(self, violation: str, bbox, track_id: int, now: float) -> bool:
        """True — событие уходит дальше; False — это дубль уже отправленного."""
        if track_id < 0:
            return True
        self._sent = [s for s in self._sent if 0 <= now - s[2] <= self.window_s]
        if any(t == violation and iou(b, bbox) >= self.min_iou for t, b, _ in self._sent):
            return False
        self._sent.append((violation, tuple(bbox), now))
        return True
