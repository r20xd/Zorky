"""Демо: свои видео как камеры по кругу. Файлы лежат на этой машине, воспроизведение — отдельным
процессом `demo/loop_video.py` (нужен opencv из ml/.venv); бэкенд только хранит и запускает."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from . import config

VIDEO_SUFFIXES = (".mp4", ".avi", ".mkv", ".mov", ".webm")
MAX_UPLOAD_BYTES = 2 * 1024**3
SAFE_NAME = re.compile(r"[^\w.\- ]+", re.UNICODE)


class DemoPlayerError(RuntimeError):
    """Действие невозможно: сообщение для человека."""


def safe_filename(name: str) -> str:
    """Только имя файла (без путей), безопасные символы, допустимое расширение."""
    base = SAFE_NAME.sub("_", Path(name.replace("\\", "/")).name).strip(" .")
    if not base or not base.lower().endswith(VIDEO_SUFFIXES):
        raise DemoPlayerError(f"нужен видеофайл: {', '.join(VIDEO_SUFFIXES)}")
    return base[:120]


def ml_python(root: Path) -> str:
    """Python с opencv: ml/.venv, иначе текущий."""
    for rel in ("ml/.venv/Scripts/python.exe", "ml/.venv/bin/python"):
        candidate = root / rel
        if candidate.is_file():
            return str(candidate)
    return sys.executable


class DemoPlayer:
    def __init__(self, home: Path | None = None, root: Path | None = None, popen=subprocess.Popen):
        self._root = root or Path(__file__).resolve().parents[3]
        self.folder = (home or Path.home() / ".mosprom") / "demo-videos"
        self._popen = popen
        self._running: dict[str, tuple[str, subprocess.Popen]] = {}

    # --- файлы -------------------------------------------------------------------------------
    def folders(self) -> list[Path]:
        if os.environ.get("MOSPROM_DEMO_ONLY") == "1":  # демо Zorky: только отобранные ролики
            return [self.folder]
        return [self.folder, self._root / "demo" / "videos"]

    def videos(self) -> list[dict]:
        found: dict[str, dict] = {}
        for folder in self.folders():
            if not folder.is_dir():
                continue
            for path in sorted(folder.iterdir()):
                if path.suffix.lower() in VIDEO_SUFFIXES and path.is_file():
                    found.setdefault(
                        path.name, {"name": path.name, "mb": round(path.stat().st_size / 1e6, 1)}
                    )
        return list(found.values())

    def path_of(self, name: str) -> Path:
        # точное имя из списка видео (без путей), иначе — очищенное, как при загрузке
        plain = Path(name.replace("\\", "/")).name
        for base in dict.fromkeys((plain, safe_filename(name))):
            for folder in self.folders():
                candidate = folder / base
                if candidate.suffix.lower() in VIDEO_SUFFIXES and candidate.is_file():
                    return candidate
        raise DemoPlayerError("такого видео нет: сначала загрузите файл")

    def target_for_upload(self, name: str) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        return self.folder / safe_filename(name)

    # --- воспроизведение ---------------------------------------------------------------------
    def _alive(self) -> None:
        for camera, (_name, proc) in list(self._running.items()):
            if proc.poll() is not None:
                del self._running[camera]

    def players(self) -> list[dict]:
        self._alive()
        return [{"camera_id": camera, "video": name} for camera, (name, _) in self._running.items()]

    def play(self, camera_id: str, video: str, backend_url: str) -> Path:
        path = self.path_of(video)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", camera_id):
            raise DemoPlayerError("id камеры: латиница, цифры, - и _ до 40 символов")
        self.stop(camera_id)
        script = self._root / "demo" / "loop_video.py"
        env = {**os.environ, "WORKER_TOKEN": config.worker_token() or ""}
        proc = self._popen(
            [
                ml_python(self._root),
                str(script),
                "--camera",
                camera_id,
                "--video",
                str(path),
                "--backend",
                backend_url,
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._running[camera_id] = (path.name, proc)
        return path

    def prepare_detect(self, camera_id: str, video: str) -> Path:
        """Камера читает файл напрямую (воркер с моделью): зацикливатель не нужен."""
        path = self.path_of(video)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", camera_id):
            raise DemoPlayerError("id камеры: латиница, цифры, - и _ до 40 символов")
        self.stop(camera_id)
        return path

    def stop(self, camera_id: str) -> bool:
        entry = self._running.pop(camera_id, None)
        if entry is None:
            return False
        proc = entry[1]
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
        return True

    def stop_all(self) -> None:
        for camera in list(self._running):
            self.stop(camera)
