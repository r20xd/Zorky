"""Что ещё не настроено для подключения к LinQ на этом компьютере: `py tasks.py linq-doctor`.

Проверяет по порядку: OpenVPN, профиль хакатона, заполненные поля доступа (только имена полей,
значения не показываются), зависимости Python, задачи планировщика (запуск без запроса UAC),
соседний VPN (Amnezia) и сам стенд. По каждому провалу печатает, что сделать.
Подробная инструкция — docs/LINQ_SETUP.md.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import vpn
from .config import ROOT, Settings, load

NEIGHBOR_VPN = re.compile(r"amnezia|wireguard|wintun|tap-windows|openvpn", re.IGNORECASE)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    required: bool = True


def module_present(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def ml_module_present(name: str) -> bool:
    """Модуль в окружении воркера ml/.venv (pika нужен ему, а не системному Python)."""
    for rel in ("ml/.venv/Scripts/python.exe", "ml/.venv/bin/python"):
        python = ROOT / rel
        if python.is_file():
            code = f"import importlib.util as u,sys;sys.exit(u.find_spec({name!r}) is None)"
            return subprocess.run([str(python), "-c", code], capture_output=True).returncode == 0
    return module_present(name)


def task_exists(task: str) -> bool:
    if sys.platform != "win32":
        return False
    done = subprocess.run(["schtasks", "/query", "/tn", f"mosprom-{task}"], capture_output=True)
    return done.returncode == 0


def list_adapters() -> list[dict]:
    """Сетевые адаптеры Windows, которые поднимают VPN: имя и состояние."""
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-NetAdapter | Select-Object Name,Status,InterfaceDescription | ConvertTo-Json",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout
        data = json.loads(out) if out.strip() else []
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return []
    return data if isinstance(data, list) else [data]


def neighbor_vpns(adapters: list[dict]) -> list[str]:
    """Названия соседних VPN-адаптеров (Amnezia, WireGuard и т.п.), кроме нашего OpenVPN."""
    found = []
    for adapter in adapters:
        text = f"{adapter.get('Name', '')} {adapter.get('InterfaceDescription', '')}"
        if NEIGHBOR_VPN.search(text) and "tap-windows" not in text.lower():
            found.append(f"{adapter.get('Name', '?')} ({adapter.get('Status', '?')})")
    return found


def missing_access(settings: Settings) -> list[str]:
    """Имена пустых полей доступа (значения не читаем и не показываем)."""
    absent = settings.missing("ovpn_user", "ovpn_pass", "jupyter_login")
    if not (settings.jupyter_pass or settings.jupyter_token):
        absent.append("JUPYTER_PASS или JUPYTER_TOKEN")
    return absent


def evaluate(
    settings: Settings,
    *,
    find_exe: Callable[[str], str] = vpn.find_openvpn,
    profile_exists: Callable[[str], bool] = lambda p: bool(p) and Path(p).is_file(),
    has_module: Callable[[str], bool] = module_present,
    has_ml_module: Callable[[str], bool] = ml_module_present,
    has_task: Callable[[str], bool] = task_exists,
    adapters: Callable[[], list[dict]] = list_adapters,
    stand_visible: Callable[[str], bool] = vpn.stand_visible,
    windows: bool = sys.platform == "win32",
) -> list[Check]:
    exe = find_exe(settings.ovpn_exe if settings.ovpn_exe != "openvpn" else "")
    profile = settings.ovpn_profile
    absent = missing_access(settings)
    checks = [
        Check(
            "OpenVPN установлен",
            bool(exe),
            exe or "openvpn.exe не найден",
            "поставьте OpenVPN Community (openvpn.net) или впишите путь в OVPN_EXE",
        ),
        Check(
            "Профиль хакатона (.ovpn)",
            profile_exists(profile),
            Path(profile).name if profile else "не найден",
            "положите .ovpn от организаторов в Загрузки или linq/, либо впишите OVPN_PROFILE",
        ),
        Check(
            "Поля доступа заполнены",
            not absent,
            "все на месте" if not absent else "пусто: " + ", ".join(absent),
            "заполните их в файле окружения (шаблон — .env.example); значения только там",
        ),
        Check(
            "websocket-client (терминал стенда)",
            has_module("websocket"),
            "есть" if has_module("websocket") else "нет",
            f"{sys.executable} -m pip install websocket-client",
        ),
        Check(
            "pika в окружении воркера (живой поток на LinQ)",
            has_ml_module("pika"),
            "есть" if has_ml_module("pika") else "нет",
            "ml/.venv/Scripts/python -m pip install pika",
            required=False,
        ),
    ]
    if windows:
        tasks = {t: has_task(t) for t in ("linq-up", "linq-down")}
        checks.append(
            Check(
                "Задачи без запроса UAC",
                all(tasks.values()),
                "есть"
                if all(tasks.values())
                else "нет: " + ", ".join(t for t, v in tasks.items() if not v),
                "один раз запустите scripts\\linq-auto-setup.bat и подтвердите запрос Windows",
            )
        )
        neighbors = neighbor_vpns(adapters())
        checks.append(
            Check(
                "Соседний VPN (Amnezia и др.)",
                True,
                ", ".join(neighbors) if neighbors else "не найден",
                "",
                required=False,
            )
        )
    visible = stand_visible(settings.host)
    checks.append(
        Check(
            f"Стенд {settings.host} виден",
            visible,
            "да, туннель работает" if visible else "нет",
            "py tasks.py linq-up (поднимет туннель и будет его держать)",
            required=False,
        )
    )
    return checks


def render(checks: list[Check]) -> str:
    lines = []
    for check in checks:
        mark = "✓" if check.ok else ("✗" if check.required else "·")
        lines.append(f"{mark} {check.name}: {check.detail}")
        if not check.ok and check.fix:
            lines.append(f"    что сделать: {check.fix}")
    failed = [c for c in checks if not c.ok and c.required]
    if failed:
        lines.append(
            f"\nНе готово: {len(failed)} из {sum(c.required for c in checks)} обязательных."
        )
    else:
        lines.append("\nВсё готово: py tasks.py linq-up")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    checks = evaluate(load())
    print(render(checks), flush=True)
    return 1 if any(not c.ok and c.required for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or None))
