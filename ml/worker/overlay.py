"""Разметка кадра для превью: рамки найденных людей и СИЗ с подписью, нарушители — красным.

Рисуем на копии кадра перед кодированием JPEG; события и снимки от этого не зависят.
Подписи — по-английски (класс детектора): шрифты OpenCV не умеют кириллицу.
"""

from __future__ import annotations

from collections.abc import Iterable

from .rules.base import Detection

# BGR. Нарушители — красные, остальные — спокойные цвета по классу.
VIOLATOR = (95, 90, 255)
CLASS_COLORS = {
    "person": (214, 226, 46),
    "helmet": (126, 211, 33),
    "head": (0, 159, 255),
    "respirator": (255, 169, 90),
    "cigarette": (0, 159, 255),
}
DEFAULT_COLOR = (200, 200, 200)


def box_pixels(bbox, width: int, height: int) -> tuple[int, int, int, int]:
    """Нормированная рамка 0..1 -> пиксели, зажатые в кадр."""
    x1, y1, x2, y2 = (min(1.0, max(0.0, v)) for v in bbox)
    return round(x1 * width), round(y1 * height), round(x2 * width), round(y2 * height)


def label_for(detection: Detection) -> str:
    """Подпись латиницей: шрифты OpenCV не рисуют кириллицу."""
    return f"{detection.cls} #{detection.track_id}" if detection.track_id >= 0 else detection.cls


def draw_detections(frame, detections: Iterable[Detection], violators: set[int] | None = None):
    """Копия кадра с рамками. violators — track_id тех, на кого сейчас сработало правило."""
    import cv2

    out = frame.copy()
    height, width = out.shape[:2]
    thickness = max(2, width // 500)
    scale = max(0.5, width / 1100)
    for detection in detections:
        bad = violators is not None and detection.track_id in violators and detection.track_id >= 0
        color = VIOLATOR if bad else CLASS_COLORS.get(detection.cls, DEFAULT_COLOR)
        x1, y1, x2, y2 = box_pixels(detection.bbox, width, height)
        cv2.rectangle(out, (x1, y1), (x2, y2), color, thickness + (1 if bad else 0))
        text = label_for(detection)
        (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
        top = max(0, y1 - th - base - 4)
        cv2.rectangle(out, (x1, top), (x1 + tw + 8, top + th + base + 4), color, -1)
        cv2.putText(
            out, text, (x1 + 4, top + th + 1), cv2.FONT_HERSHEY_SIMPLEX, scale, (13, 16, 18), 1,
            cv2.LINE_AA,
        )  # fmt: skip
    return out
