"""Настройки автоподключения к LinQ: только из окружения и файла .env (секреты не в коде)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HOST = "10.0.114.30"  # Jupyter и брокер; имя hackathon.lin-q.ru знает только DNS VPN
DEFAULT_SUBNET = "10.0.114.0 255.255.255.0"  # через OpenVPN идёт только трафик до стенда


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE построчно; пустые строки, комментарии и кавычки вокруг значения пропускаются."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def find_profile(folders: list[Path] | None = None) -> str:
    """Самый свежий *.ovpn в Загрузках (в т.ч. Telegram Desktop) и в папке linq/; пусто — нет."""
    home = Path.home()
    roots = folders or [home / "Downloads", ROOT / "linq"]
    found = []
    for root in roots:
        if root.is_dir():
            found += [p for p in root.glob("*.ovpn")] + [p for p in root.glob("*/*.ovpn")]
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(found[0]) if found else ""


@dataclass(frozen=True)
class Settings:
    ovpn_exe: str
    ovpn_profile: str
    ovpn_user: str
    ovpn_pass: str
    host: str
    subnet: str
    jupyter_url: str
    jupyter_login: str
    jupyter_pass: str
    jupyter_token: str
    broker_port: int

    @property
    def programs_dir(self) -> str:
        """Папка со скомпилированными .tpu на стороне стенда."""
        return f"/auto/cache/{self.jupyter_login}"

    def missing(self, *names: str) -> list[str]:
        """Какие из перечисленных полей (имена .env) не заполнены."""
        env_names = {
            "ovpn_profile": "OVPN_PROFILE",
            "ovpn_user": "OVPN_USER",
            "ovpn_pass": "OVPN_PASS",
            "jupyter_login": "JUPYTER_LOGIN",
        }
        return [env_names[n] for n in names if not getattr(self, n)]


def load(env: dict[str, str] | None = None, env_file: Path | None = None) -> Settings:
    """Окружение процесса важнее файла .env; `env` — подмена в тестах."""
    merged = read_env_file(env_file or ROOT / ".env")
    merged.update(os.environ if env is None else env)
    host = merged.get("LINQ_HOST") or DEFAULT_HOST
    return Settings(
        ovpn_exe=merged.get("OVPN_EXE", "") or "openvpn",
        ovpn_profile=merged.get("OVPN_PROFILE") or find_profile(),
        ovpn_user=merged.get("OVPN_USER", ""),
        ovpn_pass=merged.get("OVPN_PASS", ""),
        host=host,
        subnet=merged.get("OVPN_SUBNET") or DEFAULT_SUBNET,
        jupyter_url=(merged.get("JUPYTER_URL") or f"http://{host}").rstrip("/"),
        jupyter_login=merged.get("JUPYTER_LOGIN", ""),
        jupyter_pass=merged.get("JUPYTER_PASS", ""),
        jupyter_token=merged.get("JUPYTER_TOKEN", ""),
        broker_port=int(merged.get("LINQ_BROKER_PORT") or 5672),
    )
