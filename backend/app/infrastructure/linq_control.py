"""Управление подключением к LinQ из дашборда: статус, подключить/отключить, выбор устройства.

Сами доступы (VPN, Jupyter) здесь не хранятся и не принимаются: подключение запускает
готовую задачу планировщика Windows `mosprom-linq-up`, а она читает их из файла с доступами
на этой машине. Отчёт о стенде пишет `linq-up` (~/.mosprom/linq_report.json).
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from . import config

DEVICES = ("cpu", "gpu", "linq")
HOST = "10.0.114.30"
BROKER_PORT = 5672
LOG_NAME = "linq_up.log"
BUSY_S = 300
HOLD_S = 25  # канал бывает забит (загрузка файлов): короткий сбой проверки не считаем обрывом
SECRET_WORDS = ("pass", "парол", "token", "токен", "secret")


class LinqError(RuntimeError):
    """Действие невозможно: понятное сообщение для человека."""


def tcp_probe(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def start_task(name: str) -> bool:
    """Запустить задачу планировщика Windows; False — такой задачи нет (или не Windows)."""
    if sys.platform != "win32":
        return False
    done = subprocess.run(["schtasks", "/run", "/tn", name], capture_output=True)
    return done.returncode == 0


class LinqControl:
    def __init__(
        self,
        home: Path | None = None,
        root: Path | None = None,
        probe: Callable[[str, int], bool] = tcp_probe,
        runner: Callable[[str], bool] = start_task,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._home = home or Path.home() / ".mosprom"
        self._root = root or Path(__file__).resolve().parents[3]
        self._probe, self._runner, self._now = probe, runner, now
        self._last_ok = float("-inf")

    # --- конфигурация ------------------------------------------------------------------------
    @property
    def _config_path(self) -> Path:
        return self._home / "linq_config.json"

    def device(self) -> str:
        """Выбранное устройство; в демо по умолчанию LinQ (выбрано заранее), иначе CPU."""
        try:
            device = json.loads(self._config_path.read_text(encoding="utf-8")).get("device")
            if device in DEVICES:
                return device
        except (OSError, ValueError):
            pass
        return "linq" if config.demo_mode() else "cpu"

    def set_device(self, device: str) -> None:
        if device not in DEVICES:
            raise LinqError(f"устройство одно из {', '.join(DEVICES)}")
        self._home.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(json.dumps({"device": device}), encoding="utf-8")

    def bench(self) -> dict | None:
        """Замеры нашей программы на стенде из репозитория (docs/measured.json + linq_results)."""
        try:
            program = json.loads((self._root / "docs" / "measured.json").read_text("utf-8"))
            name = program["linq_program"]
            results = self._root / "ml" / "export" / "linq_results"
            acc = json.loads((results / f"{name}_accuracy.json").read_text("utf-8"))
        except (OSError, ValueError, KeyError):
            return None
        scored = [
            (float(k), v) for k, v in acc.get("agreement_by_tpu_conf", {}).items() if v.get("f1")
        ]
        best = max(scored, key=lambda item: item[1]["f1"]) if scored else None
        out = {
            "program": name,
            "calibration_frames": program.get("calibration_frames"),
            "latency_ms": acc.get("latency_ms"),
            "fps_by_inflight": acc.get("fps_by_inflight"),
            "best": None
            if best is None
            else {
                "conf": best[0],
                **{k: best[1].get(k) for k in ("f1", "recall", "precision")},
            },
        }
        try:  # повторный замер под нагрузкой стенда, если он снят
            loaded = json.loads((results / f"{name}_bench_loaded.json").read_text("utf-8"))
            out["loaded"] = {
                "latency_ms": loaded.get("latency_ms"),
                "fps": loaded.get("pipelined_fps"),
            }
        except (OSError, ValueError):
            pass
        return out

    # --- состояние ---------------------------------------------------------------------------
    def _report(self) -> dict | None:
        try:
            return json.loads((self._home / "linq_report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _log(self) -> tuple[list[str], bool]:
        """Последние строки журнала подключения без чувствительных слов; идёт ли подключение."""
        path = self._root / LOG_NAME
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            fresh = self._now() - path.stat().st_mtime < BUSY_S
        except OSError:
            return [], False
        lines = [
            line.strip()
            for line in text.replace("__DONE__", "").splitlines()
            if line.strip() and not any(w in line.lower() for w in SECRET_WORDS)
        ]
        return lines[-6:], fresh and "__DONE__" not in text

    def status(self) -> dict:
        if self._probe(HOST, BROKER_PORT) or self._probe(HOST, 80):
            self._last_ok = self._now()
        reachable = self._now() - self._last_ok < HOLD_S
        log, busy = self._log()
        report = self._report()
        return {
            "state": "connected" if reachable else "disconnected",
            "busy": busy,
            "host": HOST,
            "device": self.device(),
            "devices": list(DEVICES),
            "report": report,
            "bench": self.bench(),
            "ready": bool(reachable and report and report.get("ready")),
            "log": log,
        }

    # --- действия ----------------------------------------------------------------------------
    def connect(self) -> None:
        if not self._runner("mosprom-linq-up"):
            raise LinqError(
                "задача запуска не настроена: один раз запустите scripts\\linq-auto-setup.bat"
            )

    def disconnect(self) -> None:
        if not self._runner("mosprom-linq-down"):
            raise LinqError(
                "задача остановки не настроена: один раз запустите scripts\\linq-auto-setup.bat"
            )
