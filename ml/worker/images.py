"""Папка изображений как последовательный источник кадров камеры."""

from __future__ import annotations

import math
import time
from pathlib import Path


def image_paths(folder: str) -> list[Path]:
    directory = Path(folder)
    if not directory.is_dir():
        raise ValueError(f"папка изображений не найдена: {directory}")
    paths = sorted(
        (p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".png"}),
        key=lambda p: p.name,
    )
    if not paths:
        raise ValueError(f"в папке нет JPG/PNG изображений: {directory}")
    return paths


def read_images(folder: str, fps: float, loop=False, *, clock=time.monotonic, sleep=time.sleep):
    """Не пропускает картинки; медленный потребитель снижает фактический FPS.

    Время декодирования/инференса входит в интервал. После задержки нет ускоренного
    «догоняющего» проигрывания: следующий кадр не раньше чем через 1/fps от предыдущего.
    """
    import cv2

    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("--fps должен быть конечным положительным числом")
    paths = image_paths(folder)
    next_at = None
    while True:
        for path in paths:
            if next_at is not None:
                remaining = next_at - clock()
                if remaining > 0:
                    sleep(remaining)
            started = clock()
            frame = cv2.imread(str(path))
            if frame is None:
                raise ValueError(f"не удалось прочитать изображение: {path}")
            next_at = started + 1 / fps
            yield frame
        if not loop:
            return
