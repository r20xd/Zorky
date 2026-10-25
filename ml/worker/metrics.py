"""Метрики воркера для POST /api/metrics: FPS, время инференса, задержка кадр -> событие."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

METRICS_EVERY_S = 3.0


class PoseClock(threading.local):
    """Время позы в текущем потоке: камера гоняет инференс и метрики в одном потоке."""

    seconds = 0.0

    def add(self, seconds: float) -> None:
        self.seconds += seconds

    def take(self) -> float:
        value, self.seconds = self.seconds, 0.0
        return value


pose_clock = PoseClock()


class MetricsReporter:
    """Копит замеры и раз в every_s секунд отдаёт тело метрик (время передаётся снаружи)."""

    def __init__(self, camera_id: str, device: str = "cpu", every_s: float = METRICS_EVERY_S):
        self.camera_id = camera_id
        self.device = device
        self.every_s = every_s
        self.started: float | None = None
        self.frames = 0
        self.inference: list[float] = []
        self.e2e: list[float] = []
        self.pose: list[float] = []
        self.last_pose_ms: float | None = None  # поза отдельно: контракт Metrics поля не имеет

    def frame(self, inference_s: float, pose_s: float = 0.0) -> None:
        self.frames += 1
        self.inference.append(inference_s * 1000)
        if pose_s > 0:
            self.pose.append(pose_s * 1000)

    def event(self, e2e_s: float) -> None:
        self.e2e.append(e2e_s * 1000)

    def report(self, now: float) -> dict | None:
        """Тело Metrics, если пора, и сброс окна; иначе None."""
        if self.started is None:
            self.started = now
            return None
        elapsed = now - self.started
        if elapsed < self.every_s or not self.frames:
            return None
        body = {
            "camera_id": self.camera_id,
            "device": self.device,
            "fps": round(self.frames / elapsed, 2),
            "inference_ms": round(sum(self.inference) / len(self.inference), 2),
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        }
        if self.e2e:
            body["e2e_ms"] = round(sum(self.e2e) / len(self.e2e), 2)
        self.last_pose_ms = round(sum(self.pose) / len(self.pose), 2) if self.pose else None
        self.started, self.frames, self.inference, self.e2e, self.pose = now, 0, [], [], []
        return body
