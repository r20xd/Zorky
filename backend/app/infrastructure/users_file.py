"""Пользователи в JSON-файле вне git (MOSPROM_USERS, по умолчанию ~/.mosprom/users.json).

Формат файла: {login: {name, role, salt, hash, ...}}, salt и hash — hex, пароль — scrypt.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def load_users(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_users(path: Path, users: dict[str, dict]) -> None:
    """Атомарно: временный файл рядом и замена."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        path.chmod(0o600)
    except OSError:
        pass


class JsonUserRepository:
    """Набор пользователей из файла; изменился снаружи (по времени файла) — перечитывается."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._users: dict[str, dict] = {}
        self._mtime: float | None = None

    @property
    def location(self) -> str:
        return str(self.path)

    def current(self) -> dict[str, dict]:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime != self._mtime:
            self._mtime = mtime
            self._users = load_users(self.path) if mtime is not None else {}
        return self._users

    def save(self, users: dict[str, dict]) -> None:
        save_users(self.path, users)
        self._mtime = None  # следующее чтение перечитает файл
        self._users = users
