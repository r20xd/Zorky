"""Реестр типов нарушений (contracts/violation_types.json): код -> {name, severity}."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import config

log = logging.getLogger("notify")


def load_registry(path: Path | None = None) -> dict[str, dict]:
    """Нет файла или он битый — пустой реестр (критичные типы тогда берутся из запасного набора)."""
    try:
        data = json.loads((path or config.registry_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("реестр нарушений не прочитан — берём запасной набор критичных типов")
        return {}
    return {t["code"]: t for t in data.get("types", [])}
