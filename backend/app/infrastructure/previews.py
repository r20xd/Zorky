"""Превью камер: последний JPEG от воркера в памяти, раздача как MJPEG (кадры не декодируем)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable

BOUNDARY = "frame"


class Previews:
    """Последний кадр каждой камеры; номер кадра растёт, чтобы читатель не слал один кадр дважды."""

    def __init__(self) -> None:
        self._frames: dict[str, tuple[int, bytes]] = {}
        self._worker_at: dict[str, float] = {}

    def put(self, camera_id: str, jpeg: bytes) -> None:
        seq = self._frames.get(camera_id, (0, b""))[0] + 1
        self._frames[camera_id] = (seq, jpeg)

    def put_from_worker(self, camera_id: str, jpeg: bytes) -> None:
        """Кадр воркера (с рамками): запоминаем время, сырые кадры телефона его не перебивают."""
        self._worker_at[camera_id] = time.monotonic()
        self.put(camera_id, jpeg)

    def worker_active(self, camera_id: str, within_s: float = 3.0) -> bool:
        return time.monotonic() - self._worker_at.get(camera_id, float("-inf")) < within_s

    def latest(self, camera_id: str) -> tuple[int, bytes] | None:
        return self._frames.get(camera_id)


def is_jpeg(data: bytes) -> bool:
    return data[:2] == b"\xff\xd8"


def part(jpeg: bytes) -> bytes:
    head = f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n"
    return head.encode() + jpeg + b"\r\n"


async def mjpeg_stream(
    previews: Previews,
    camera_id: str,
    is_disconnected: Callable[[], Awaitable[bool]],
    poll_s: float = 0.05,
) -> AsyncIterator[bytes]:
    """Каждый новый кадр камеры — частью multipart; пока кадров нет, ждём."""
    last = 0
    while not await is_disconnected():
        current = previews.latest(camera_id)
        if current is None or current[0] == last:
            await asyncio.sleep(poll_s)
            continue
        last = current[0]
        yield part(current[1])
