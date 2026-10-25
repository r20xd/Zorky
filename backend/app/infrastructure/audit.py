"""Журнал аудита действий с пользователями: строка JSON на событие, без паролей и хешей."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..domain.users import now_iso
from . import config

log = logging.getLogger("mosprom.auth")


class FileAuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    def record(self, actor: str, action: str, target: str, **details: object) -> None:
        line = {"ts": now_iso(), "actor": actor, "action": action, "target": target, **details}
        path = self._path or config.audit_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            log.exception("не записан журнал аудита")
