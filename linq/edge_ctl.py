"""Живой поток на LinQ: сервис кадров на стенде + воркер на ноутбуке.

    py tasks.py linq-edge start|stop|status [--program ppe16_b1_o5_h1m.tpu]
    py tasks.py linq-live  [--program ...]    # сервис + воркер на всех камерах бэкенда

Воркер (ml/worker, `LINQ_MODE=edge`) сжимает кадр в JPEG, шлёт его сервису на стенде через
брокер RabbitMQ, сервис гонит кадр через программу .tpu и возвращает рамки. Нужен туннель до
стенда (`py tasks.py linq-up`). Метрики воркера идут в бэкенд с device=linq, в дашборде это
видно в разделе «LinQ».
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .config import ROOT, Settings, load
from .jupyter import Jupyter, connect

SHELL = "$HOME/work/mosprom/edge"
REMOTE = "mosprom/edge"
PROGRAMS = ROOT / "ml" / "linq_programs"
DEFAULT_PROGRAM = "ppe16_b1_o5_h1m.tpu"
# Имена классов нашей модели СИЗ; нужны воркеру, если компилятор не записал их в .tpu.json
PPE_NAMES = {"0": "person", "1": "helmet", "2": "head", "3": "respirator", "4": "cigarette"}
DEMO_TOKEN = "demo-worker-token"  # тот же, что в scripts/demo.ps1; настоящий берётся из окружения


def queue_name(login: str) -> str:
    return f"mosprom.edge.{login}"


def start_command(login: str, program: str, threads: int = 3) -> str:
    """Запуск сервиса отдельным процессом (setsid: не умирает вместе с терминалом Jupyter)."""
    return (
        f"mkdir -p {SHELL} && cd {SHELL} && pkill -f 'edge_service.py {login}' ; sleep 1; "
        f"setsid nohup python3 edge_service.py {login} {program} --threads {threads} "
        f"> edge.log 2>&1 < /dev/null & sleep 2; echo LAUNCHED"
    )


def status_command(login: str) -> str:
    """Метки разорваны кавычками: терминал печатает эхом саму команду (см. build_program)."""
    return (
        f'pgrep -f "edge_service.py {login}" >/dev/null && echo RUN""NING=1; '
        f"tail -n 3 {SHELL}/edge.log"
    )


def parse_status(text: str) -> bool:
    return "RUNNING=1" in text


def ensure_program_json(program: str, jupyter: Jupyter | None, login: str) -> Path:
    """Описание программы рядом с путём, который читает воркер: вход, выходы и имена классов."""
    PROGRAMS.mkdir(parents=True, exist_ok=True)
    local = PROGRAMS / f"{program}.json"
    data: dict = {}
    if local.is_file():
        data = json.loads(local.read_text(encoding="utf-8"))
    elif jupyter is not None:
        remote = f"{jupyter_cache(login)}/{program}.json"
        code, out = jupyter.run(f"cat {remote}", timeout=60)
        start = out.find("{")
        data = json.loads(out[start : out.rfind("}") + 1]) if code == 0 and start >= 0 else {}
    if not data.get("input") or not data.get("outputs"):
        raise SystemExit(
            f"нет описания {program}.json: соберите программу (linq-build) или скачайте"
        )
    if not data.get("names"):
        data["names"] = PPE_NAMES
    local.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return local.with_suffix("")  # путь к программе; .json лежит рядом


def jupyter_cache(login: str) -> str:
    return f"/auto/cache/{login}"


def say(text: str) -> None:
    print(text, flush=True)


def edge_start(s: Settings, program: str, threads: int = 6) -> None:
    jupyter = connect(s)
    for name in ("linq/edge_service.py", "ml/worker/detect.py"):
        jupyter.upload_file(ROOT / name, REMOTE)
    code, out = jupyter.run(start_command(s.jupyter_login, program, threads), timeout=90)
    if "LAUNCHED" not in out:
        raise SystemExit(f"сервис не запустился (код {code}): {out[-300:]}")
    code, out = jupyter.run(status_command(s.jupyter_login), timeout=60)
    if not parse_status(out):
        raise SystemExit(f"сервис не стартовал: {out[-400:]}")
    say(f"сервис кадров на стенде работает, очередь {queue_name(s.jupyter_login)}")


def edge_stop(s: Settings) -> None:
    jupyter = connect(s)
    jupyter.run(f"pkill -f 'edge_service.py {s.jupyter_login}'; echo STOPPED", timeout=60)
    say("сервис кадров остановлен")


def edge_status(s: Settings) -> bool:
    _code, out = connect(s).run(status_command(s.jupyter_login), timeout=60)
    running = parse_status(out)
    say(f"сервис кадров: {'работает' if running else 'не запущен'}")
    return running


def ml_python() -> str:
    for rel in ("ml/.venv/Scripts/python.exe", "ml/.venv/bin/python"):
        if (ROOT / rel).is_file():
            return str(ROOT / rel)
    return sys.executable


def stop_local_workers() -> int:
    """Останавливает воркеры, уже читающие камеры бэкенда (демо на CPU): двое сразу дают дубли."""
    if sys.platform != "win32":
        return 0
    found = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and "
            "$_.CommandLine -match 'worker\\.main' } | ForEach-Object { $_.ProcessId }",
        ],
        capture_output=True,
        text=True,
    ).stdout.split()
    for pid in found:
        subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True)
    return len(found)


def worker_command(program_path: Path, backend: str, fps: float) -> list[str]:
    return [
        ml_python(),
        "-m",
        "worker.main",
        "--cameras-from-backend",
        "--loop",
        "--model",
        str(program_path),
        "--ppe-weights",
        str(program_path),
        "--device",
        "linq",
        "--fps",
        str(fps),
        "--backend",
        backend,
    ]


def worker_env(login: str, size: int, quality: int) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("WORKER_TOKEN", DEMO_TOKEN)
    env.update(
        LINQ_MODE="edge",
        LINQ_EDGE_QUEUE=queue_name(login),
        LINQ_EDGE_SIZE=str(size),
        LINQ_EDGE_QUALITY=str(quality),
    )
    return env


def live(
    s: Settings, program: str, backend: str, fps: float, size: int, quality: int, threads: int = 6
) -> None:
    jupyter = connect(s)
    path = ensure_program_json(program, jupyter, s.jupyter_login)
    edge_start(s, program, threads)
    stopped = stop_local_workers()
    if stopped:
        say(f"остановил прежних воркеров: {stopped}")
    out, err = ROOT / "worker.log", ROOT / "worker.err.log"
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0  # DETACHED | NEW_GROUP
    with out.open("ab") as stdout, err.open("ab") as stderr:
        proc = subprocess.Popen(
            worker_command(path, backend, fps),
            cwd=ROOT / "ml",
            env=worker_env(s.jupyter_login, size, quality),
            stdout=stdout,
            stderr=stderr,
            creationflags=flags,
        )
    say(f"воркер на LinQ запущен (pid {proc.pid}); журнал worker.log; камеры берёт из бэкенда")
    say("в дашборде раздел «LinQ»: устройство LinQ у камер появится через 10-20 секунд")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("start", "stop", "status", "live"))
    parser.add_argument("--program", default=DEFAULT_PROGRAM)
    parser.add_argument("--backend", default="http://127.0.0.1:8080")
    parser.add_argument("--fps", type=float, default=4.0)
    parser.add_argument("--threads", type=int, default=6, help="потоков сервиса на стенде")
    parser.add_argument("--size", type=int, default=640, help="сторона отправляемого кадра")
    parser.add_argument("--quality", type=int, default=75, help="качество JPEG")
    args = parser.parse_args(argv)
    s = load()
    if args.action == "start":
        edge_start(s, args.program, args.threads)
    elif args.action == "stop":
        edge_stop(s)
    elif args.action == "status":
        edge_status(s)
    else:
        live(s, args.program, args.backend, args.fps, args.size, args.quality, args.threads)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
