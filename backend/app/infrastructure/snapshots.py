"""Снимки нарушений: JPEG-файлы в папке, отдаются по имени вида snap-<12 hex>.jpg."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

SNAPSHOT_NAME = re.compile(r"snap-[0-9a-f]{12}\.jpg")


class SnapshotStorage:
    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def save(self, jpeg: bytes) -> str:
        """Сохраняет снимок и возвращает его адрес для события."""
        name = f"snap-{uuid.uuid4().hex[:12]}.jpg"
        self._folder.mkdir(parents=True, exist_ok=True)
        (self._folder / name).write_bytes(jpeg)
        return f"/api/snapshots/{name}"

    def read(self, name: str) -> bytes | None:
        """Байты снимка; чужое имя (не наш формат) или нет файла — None."""
        path = self._folder / name
        if not SNAPSHOT_NAME.fullmatch(name) or not path.is_file():
            return None
        return path.read_bytes()
