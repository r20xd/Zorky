"""Настройки из окружения: где лежат база, снимки, пользователи, реестр нарушений."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_WINDOW_S = 30.0
DEFAULT_ESCALATE_S = 60.0


def _home() -> Path:
    return Path.home() / ".mosprom"


def db_path() -> str:
    """Файл SQLite: MOSPROM_DB (":memory:" — без сохранения), иначе ~/.mosprom/mosprom.db."""
    return os.environ.get("MOSPROM_DB") or str(_home() / "mosprom.db")


def snapshots_dir() -> Path:
    """Папка снимков: MOSPROM_SNAPSHOTS, иначе ~/.mosprom/snapshots (вне git)."""
    return Path(os.environ.get("MOSPROM_SNAPSHOTS") or _home() / "snapshots")


def users_path() -> Path:
    return Path(os.environ.get("MOSPROM_USERS") or _home() / "users.json")


def audit_path() -> Path:
    return Path(os.environ.get("MOSPROM_AUDIT") or _home() / "audit.log")


def registry_path() -> Path:
    """Реестр нарушений: MOSPROM_TYPES, иначе contracts/violation_types.json в репозитории."""
    return Path(
        os.environ.get("MOSPROM_TYPES")
        or Path(__file__).parents[3] / "contracts" / "violation_types.json"
    )


def worker_token() -> str | None:
    return os.environ.get("WORKER_TOKEN") or None


def incident_window_s() -> float:
    """Окно склейки повторов в один инцидент, секунды: MOSPROM_INCIDENT_WINDOW_S."""
    try:
        return float(os.environ.get("MOSPROM_INCIDENT_WINDOW_S", DEFAULT_WINDOW_S))
    except ValueError:
        return DEFAULT_WINDOW_S


def escalate_s() -> float:
    """Через сколько секунд молчания мастера сообщить операторам: ESCALATE_S."""
    try:
        return float(os.environ.get("ESCALATE_S", DEFAULT_ESCALATE_S))
    except ValueError:
        return DEFAULT_ESCALATE_S


def port() -> int:
    """Порт бэкенда на этой машине: PORT (как у `tasks.py`), по умолчанию 8080."""
    try:
        return int(os.environ.get("PORT", "8080"))
    except ValueError:
        return 8080


def demo_only() -> bool:
    """MOSPROM_DEMO_ONLY=1: демо Zorky — без выдуманных камер, в списке только отобранные ролики."""
    return os.environ.get("MOSPROM_DEMO_ONLY") == "1"


def demo_mode() -> bool:
    """MOSPROM_DEMO=1: вход в один клик без пароля, но только для пользователей, созданных демо."""
    return os.environ.get("MOSPROM_DEMO") == "1"
