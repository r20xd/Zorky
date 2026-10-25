#!/usr/bin/env python3
"""Задачи репозитория: гейты зон, демо, e2e, установка.

Windows:   py tasks.py <задача>
Mac/Linux: python3 tasks.py <задача>   (или make <задача>)

  setup         venv для ml/ и backend/, npm-зависимости web/, git-хуки
  gate-ml       ruff + pytest (ml/, demo/, tasks.py)
  gate-back     ruff + pytest (backend/) + e2e
  gate-web      tsc + vite build + vitest (web/)
  gate          все три гейта
  gate-changed  гейты только изменённых зон (можно повесить на pre-push, см. .githooks)
  demo          бэкенд + фронт + мок событий по кругу; --no-mock — без мока;
                --login — со входом (оператор и мастер, пароли в demo/README.md); Ctrl+C — стоп
  run --video <файл|RTSP> [--video ...] [--ppe-weights W] [--device auto|cpu|gpu|linq]
  run --from-backend [--ppe-weights W] [--device D]
  run --no-docker ...  то же без Docker: uvicorn + vite preview на https://<IP>:8443
                бэкенд + nginx (docker compose) на https://localhost:8443 со входом
                + воркер на камеру (cam-1, cam-2 ...); Ctrl+C — стоп
  cert          свой сертификат на localhost и IP ноутбука в ~/.mosprom/certs (не в git)
  e2e           бэкенд на тестовом порту + мок событий + проверка, что события дошли до API

LinQ (доступы — в файле с доступами, шаблон .env.example; OpenVPN — от администратора):
  linq-up           OpenVPN только до стенда (рядом с Amnezia) → Jupyter → загрузка .tpu → проверка
  linq-check        то же без запуска VPN и без загрузки: что уже есть на стенде
  linq-watch        linq-up и остаться: при обрыве туннель поднимается сам
  linq-doctor       что не настроено для подключения к LinQ на этом компьютере
  linq-build        start <веса.onnx> | status | finish: веса -> .tpu на стенде -> проверка качества
  linq-edge         start | stop | status: сервис кадров на стенде (JPEG -> ускоритель -> рамки)
  linq-live         живой поток на LinQ: сервис + воркер на всех камерах бэкенда
  linq-down         остановить OpenVPN и наблюдателя


Когда вывод идёт не в терминал, команды пишут коротко: зелёный шаг — строка, красный — хвост.
Полный вывод — MOSPROM_VERBOSE=1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ML = ROOT / "ml"
BACKEND = ROOT / "backend"
WEB = ROOT / "web"
DEMO = ROOT / "demo"
IS_WINDOWS = os.name == "nt"
PY = "py" if IS_WINDOWS else "python3"
# В терминале вывод виден целиком; вне терминала печатаем только итог и хвост ошибки
VERBOSE = os.environ.get("MOSPROM_VERBOSE") == "1" or sys.stdout.isatty()
TAIL_LINES = 60

INSTALL_HINTS = {
    "npm": "поставь Node.js 22+ с https://nodejs.org",
    "git": "поставь git",
}

# Какие гейты нужны при изменении файла: префикс пути -> зоны.
# Ровно как фильтр в .github/workflows/ci.yml (сверяет tests/test_ci_workflow.py)
ZONE_PREFIXES = {
    "contracts/": ("ml", "back", "web"),
    "ml/": ("ml",),
    "demo/": ("ml", "back"),  # events.sample.json читают контрактный тест и e2e бэкенда
    "tasks.py": ("ml", "back", "web"),
    "tests/": ("ml",),
    "ruff.toml": ("ml", "back"),
    "backend/": ("back",),
    "web/": ("web",),
}


class TaskError(Exception):
    """Понятная человеку ошибка: печатаем и выходим с кодом 1."""


def say(message: str) -> None:
    print(message, flush=True)


def tail(text: str, lines: int) -> str:
    return "\n".join(text.rstrip().splitlines()[-lines:])


def where(cwd: Path) -> str:
    return "." if cwd == ROOT else cwd.relative_to(ROOT).as_posix()


def run(cmd: list, cwd: Path = ROOT, env: dict | None = None) -> None:
    line = f"{Path(str(cmd[0])).stem} {' '.join(str(c) for c in cmd[1:])}   [{where(cwd)}]"
    args = [str(c) for c in cmd]
    if VERBOSE:
        say(f"$ {line}")
        result = subprocess.run(args, cwd=cwd, env=env, check=False)
    else:
        started = time.monotonic()
        result = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            say(f"✓ {line} {time.monotonic() - started:.0f} с")
        else:
            say(f"✗ {line}\n{tail(result.stdout, TAIL_LINES)}")
    if result.returncode != 0:
        raise TaskError(f"{Path(str(cmd[0])).stem} завершился с кодом {result.returncode}")


def need(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        raise TaskError(f"не найден {tool}: {INSTALL_HINTS.get(tool, 'установи его')}")
    return path


def child_env(**extra: str) -> dict:
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    env.update(extra)
    return env


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


# --- зависимости -------------------------------------------------------------


def zone_python(zone: Path, venv: str = ".venv") -> Path:
    return zone / venv / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def requirements_hash(zone: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(zone.glob("requirements*.txt")):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def ensure_env(zone: Path, venv: str = ".venv", clean: bool = False) -> Path:
    """<зона>/<venv> с requirements-dev.txt; переустанавливает, если requirements*.txt изменились.

    clean — при переустановке venv создаётся заново, чтобы старые пакеты не оставались.
    """
    python = zone_python(zone, venv)
    stamp = zone / venv / ".requirements-hash"
    if stamp.exists() and stamp.read_text() == requirements_hash(zone):
        return python
    if clean:
        shutil.rmtree(zone / venv, ignore_errors=True)
    if not python.exists():
        run([sys.executable, "-m", "venv", zone / venv])
    pip = [python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    run([*pip, "-r", "requirements-dev.txt"], cwd=zone, env=child_env())
    stamp.write_text(requirements_hash(zone))
    return python


def gate_env(zone: Path) -> Path:
    """Чистый <зона>/.venv-gate только с requirements-dev.txt — как в CI.

    Рабочий .venv гейту не годится: туда ставят requirements.txt и пакеты руками,
    и гейт зеленеет на пакете, которого CI не поставит.
    """
    return ensure_env(zone, ".venv-gate", clean=True)


def ensure_web_deps() -> str:
    """npm ci, если node_modules нет или package-lock.json изменился."""
    npm = need("npm")
    lock = WEB / "package-lock.json"
    stamp = WEB / "node_modules" / ".lock-hash"
    if not stamp.exists() or stamp.read_text() != file_hash(lock):
        run([npm, "ci", "--no-audit", "--no-fund"], cwd=WEB)
        stamp.write_text(file_hash(lock))
    return npm


# --- гейты -------------------------------------------------------------------


def python_gate(zone: Path, lint_targets: list[str]) -> None:
    python = gate_env(zone)
    env = child_env()
    run([python, "-m", "ruff", "check", *lint_targets], env=env)
    run([python, "-m", "ruff", "format", "--check", *lint_targets], env=env)
    run([python, "-m", "pytest"], cwd=zone, env=env)


def gate_ml() -> None:
    python_gate(ML, ["ml", "demo", "tasks.py", "tests"])
    run([gate_env(ML), "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], env=child_env())


def gate_back() -> None:
    python_gate(BACKEND, ["backend"])
    e2e()


def gate_web() -> None:
    npm = ensure_web_deps()
    run([npm, "run", "build"], cwd=WEB)
    run([npm, "test"], cwd=WEB)


GATES = {"ml": gate_ml, "back": gate_back, "web": gate_web}


def run_gates(zones: list[str]) -> None:
    failed = []
    for zone in zones:
        say(f"\n=== gate-{zone} ===")
        try:
            GATES[zone]()
        except TaskError as err:
            say(f"!!! gate-{zone}: {err}")
            failed.append(zone)
    if failed:
        raise TaskError("красные гейты: " + ", ".join(f"gate-{zone}" for zone in failed))
    say("\nOK: " + ", ".join(f"gate-{zone}" for zone in zones))


def git_lines(*args: str) -> list[str] | None:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def changed_files() -> list[str] | None:
    """Файлы из коммитов, которых нет в upstream, и незакоммиченные. None — не с чем сравнить."""
    base = "@{upstream}"
    if git_lines("rev-parse", "--abbrev-ref", base) is None:
        base = REMOTE_MAIN  # новая ветка без upstream: от origin/main
        if git_lines("rev-parse", "--verify", "--quiet", base) is None:
            return None
    committed = git_lines("diff", "--name-only", f"{base}...HEAD")
    status = git_lines("status", "--porcelain", "--untracked-files=all")
    if committed is None or status is None:
        return None
    pending = [line[3:].split(" -> ")[-1].strip('"') for line in status]
    return sorted(set(committed) | set(pending))


def zones_for(files: list[str]) -> list[str]:
    zones: set[str] = set()
    for path in files:
        for prefix, prefix_zones in ZONE_PREFIXES.items():
            if path == prefix or path.startswith(prefix):
                zones.update(prefix_zones)
    return [zone for zone in GATES if zone in zones]


def gate_changed() -> None:
    files = changed_files()
    if files is None:
        say("upstream не найден — гоняю все гейты")
        run_gates(list(GATES))
        return
    zones = zones_for(files)
    if not zones:
        say("в зонах с гейтами ничего не менялось — гейт не нужен")
        return
    run_gates(zones)


# --- процессы для demo и e2e -------------------------------------------------


def start(cmd: list, cwd: Path, env: dict, log: Path | None = None) -> subprocess.Popen:
    """Фоновый процесс; с log — вывод в файл, а не в консоль."""
    say(f"$ {Path(str(cmd[0])).stem} {' '.join(str(c) for c in cmd[1:])}   [{where(cwd)}]")
    if IS_WINDOWS:
        group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        group = {"start_new_session": True}
    output = {}
    if log is not None:
        output = {"stdout": log.open("wb"), "stderr": subprocess.STDOUT}
    return subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=env, **group, **output)


def stop(proc: subprocess.Popen) -> None:
    """Останавливает процесс со всеми потомками (npm запускает дочерние процессы)."""
    if proc.poll() is not None:
        return
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False
        )
    else:
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def wait_http(url: str, timeout: float, proc: subprocess.Popen) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise TaskError(f"процесс завершился раньше, чем ответил {url}")
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.3)
    raise TaskError(f"{url} не ответил за {timeout:.0f} с")


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read())


def ensure_port_free(port: str) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if probe.connect_ex(("127.0.0.1", int(port))) == 0:
            raise TaskError(
                f"порт {port} занят другим процессом — запусти на другом: "
                f"PORT=8090 (PowerShell: $env:PORT=8090; cmd: set PORT=8090)"
            )


def start_backend(
    port: str, env: dict, log: Path | None = None, python: Path | None = None
) -> subprocess.Popen:
    ensure_port_free(port)
    python = python or ensure_env(BACKEND)
    uvicorn = [python, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", port]
    return start(uvicorn, BACKEND, env, log)


DEMO_CAMERAS = {"cam-1": DEMO / "videos" / "cam1.mp4", "cam-2": DEMO / "videos" / "cam2.mp4"}


def demo_videos() -> dict[str, Path]:
    """Ролики демо по камерам; пусто, если нет хотя бы одного (они лежат вне git)."""
    return DEMO_CAMERAS if all(path.exists() for path in DEMO_CAMERAS.values()) else {}


# Демо со входом: два настоящих ролика стоят на паузе, пока админ не пустит их в «Демо-видео».
# Ролик 1 — открытый ролик без срабатываний системы; ролик 2 — настоящее падение.
DEMO_CLIPS = {
    "Ролик 1 - без происшествий.mp4": DEMO / "clips" / "Ролик 1 - без происшествий.mp4",
    "Ролик 2 - падение человека.mp4": DEMO / "clips" / "Ролик 2 - падение человека.mp4",
}


def prepare_demo_clips(folder: Path) -> list[str]:
    """Кладёт два демо-ролика в папку демо-видео; возвращает имена тех, что скопированы."""
    folder.mkdir(parents=True, exist_ok=True)
    copied = []
    for name, source in DEMO_CLIPS.items():
        target = folder / name
        if source.exists() and not target.exists():
            shutil.copyfile(source, target)
        if target.exists():
            copied.append(name)
    return copied


DEMO_USERS = {  # демо-пароли: только для локального показа, в базе их нет
    "operator": ("operator", "Оператор", "demo-operator-1"),
    "master": ("master", "Мастер", "demo-master-1"),
    "specialist": ("specialist", "Специалист ОТ", "demo-specialist-1"),
    "manager": ("manager", "Руководитель", "demo-manager-1"),
    "admin": ("admin", "Администратор", "demo-admin-1"),
}
DEMO_WORKER_TOKEN = "demo-worker-token"


def write_demo_users(path: Path) -> None:
    """Файл пользователей демо со входом: оператор и мастер (формат — users_file.py бэкенда)."""
    users = {}
    for login, (role, name, password) in DEMO_USERS.items():
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        users[login] = {
            "name": name,
            "role": role,
            "salt": salt.hex(),
            "hash": digest.hex(),
            "active": True,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "created_by": "demo",
        }
    path.write_text(json.dumps(users, ensure_ascii=False), encoding="utf-8")


def web_dist_fresh() -> bool:
    """Собранный сайт (web/dist) свежее исходников: пересборка не нужна."""
    index = WEB / "dist" / "index.html"
    if not index.is_file():
        return False
    built = index.stat().st_mtime
    sources = [WEB / "index.html", WEB / "package.json", WEB / "vite.config.ts"]
    for folder in (WEB / "src", WEB / "public"):
        sources += [p for p in folder.rglob("*") if p.is_file()]
    return all(p.stat().st_mtime <= built for p in sources if p.exists())


def start_demo_web(npm: str, backend_url: str, dev: bool) -> subprocess.Popen:
    """Сайт демо: готовая сборка (vite preview, лёгкая) или dev-сервер (--dev, с горячей правкой).

    На слабом ноутбуке dev-сервер ест память и процессор и отдаёт сотни модулей по одному,
    что забивает 6 соединений браузера; сборка весит один бандл."""
    env = child_env(VITE_API_TARGET=backend_url)
    if dev:
        return start([npm, "run", "dev"], WEB, env)
    if not web_dist_fresh():
        say("собираю сайт для демо (один раз, ~30 с)...")
        run([npm, "exec", "--", "vite", "build"], cwd=WEB, env=env)
    return start(
        [
            npm,
            "exec",
            "--",
            "vite",
            "preview",
            "--host",
            "127.0.0.1",
            "--port",
            "5173",
            "--strictPort",
        ],
        WEB,
        env,
    )


def demo_ppe_weights() -> Path | None:
    """Веса СИЗ рядом, как default_ppe_weights (ml/worker/main.py): переменная, ~/.mosprom, ml."""
    env = os.environ.get("MOSPROM_PPE_WEIGHTS")
    candidates = [Path(env)] if env else []
    candidates += [
        ROOT / "models" / "best.onnx",
        mosprom_home() / "models" / "best.onnx",
        ML / "weights" / "best.pt",
    ]
    return next((path for path in candidates if path.is_file()), None)


def demo_worker_command(backend_url: str) -> list | None:
    """Воркер демо с камерами из бэкенда (рамки и нарушения на роликах) или None, если нечем."""
    weights = demo_ppe_weights()
    if weights is None or os.environ.get("MOSPROM_DEMO_WORKER") == "0":
        return None
    python = zone_python(ML, ".venv")
    if not python.exists():
        return None
    return [
        python, "-m", "worker.main", "--cameras-from-backend", "--loop",
        "--model", weights, "--ppe-weights", weights,
        "--device", "cpu", "--fps", "4", "--backend", backend_url,
    ]  # fmt: skip


def demo(mock: bool, login: bool = False, dev: bool = False) -> None:
    npm = ensure_web_deps()
    port = os.environ.get("PORT", "8080")
    backend_url = f"http://127.0.0.1:{port}"
    ensure_port_free(port)  # до чистки папки: занятый порт не должен стирать users.json живого демо
    mock = mock and not login  # со входом показ начинается с нуля: ни роликов, ни мок-событий
    videos = demo_videos() if mock else {}
    env = child_env()
    worker_env = child_env()
    if videos or login:
        # свежая база: показ начинается с нуля нарушений, прошлые прогоны не мешают
        run_dir = mosprom_home() / "demo-video"
        # перезапуск сторожем (MOSPROM_DEMO_KEEP=1) не стирает базу посреди показа
        keep = os.environ.get("MOSPROM_DEMO_KEEP") == "1" and (run_dir / "events.sqlite").exists()
        if not keep:
            shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        extra = {"MOSPROM_DB": str(run_dir / "events.sqlite")}
        extra["MOSPROM_SNAPSHOTS"] = str(run_dir / "snapshots")
        if login:
            write_demo_users(run_dir / "users.json")
            extra.update(
                MOSPROM_USERS=str(run_dir / "users.json"),
                WORKER_TOKEN=DEMO_WORKER_TOKEN,
                MOSPROM_DEMO="1",  # вход в один клик без пароля, только для пользователей демо
                MOSPROM_DEMO_ONLY="1",  # в «Демо-видео» только два отобранных ролика
                # ролики идут по кругу: повторы длящегося нарушения склеиваем в один инцидент
                MOSPROM_INCIDENT_WINDOW_S=os.environ.get("MOSPROM_INCIDENT_WINDOW_S", "600"),
                ESCALATE_S=os.environ.get("ESCALATE_S", "300"),
            )
            clips = prepare_demo_clips(mosprom_home() / "demo-videos")
            if clips:
                say("демо-ролики: " + "; ".join(clips))
            worker_env = child_env(WORKER_TOKEN=DEMO_WORKER_TOKEN)
        env = child_env(**extra)
    procs: list[subprocess.Popen] = []
    aux: list[subprocess.Popen] = []
    try:
        procs.append(start_backend(port, env))
        wait_http(f"{backend_url}/healthz", timeout=60, proc=procs[0])
        procs.append(start_demo_web(npm, backend_url, dev))
        if videos:
            # ролики по кругу вместо камер, нарушения по одному и редко (opencv — в ml/.venv)
            cams = [arg for cam, path in videos.items() for arg in ("--cam", f"{cam}={path}")]
            replay = [ensure_env(ML), DEMO / "replay_video.py", "--backend", backend_url, *cams]
            procs.append(start(replay, ROOT, worker_env))
        elif mock:
            replay = [sys.executable, DEMO / "replay_events.py", "--loop", "--backend", backend_url]
            procs.append(start(replay, ROOT, worker_env))
        worker_note = None
        if login:
            cmd = demo_worker_command(backend_url)
            if cmd:
                # падение воркера не должно ронять показ: он не входит в проверяемые procs
                aux.append(start(cmd, ML, worker_env))
                say("воркер с моделью СИЗ запущен (камеры из бэкенда, распознавание на процессоре)")
            else:
                worker_note = (
                    "воркер не запущен (нет весов СИЗ models/best.onnx или ml/.venv): "
                    "ролики с «Распознавать нарушения» не покажут кадров. "
                    f"Подготовьте: {PY} tasks.py setup и положите best.onnx, "
                    "либо запустите scripts/demo.bat"
                )
        say("\nдемо запущено: http://localhost:5173 — Ctrl+C остановит всё")
        if login:
            for user, (_, name, password) in DEMO_USERS.items():
                say(f"  вход: {user} / {password} ({name})")
            if worker_note:
                say("  внимание: " + worker_note)
        while all(proc.poll() is None for proc in procs):
            time.sleep(1)
        raise TaskError("один из процессов демо завершился — смотри вывод выше")
    except KeyboardInterrupt:
        say("\nостанавливаю...")
    finally:
        for proc in reversed([*procs, *aux]):
            stop(proc)


def mosprom_home() -> Path:
    return Path(os.environ.get("MOSPROM_HOME") or Path.home() / ".mosprom")


def laptop_ip() -> str | None:
    """IP ноутбука в локальной сети (пакеты не отправляются)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("10.255.255.255", 1))
            return probe.getsockname()[0]
        except OSError:
            return None


def cert_command(cert: Path, key: Path, ip: str | None) -> list:
    names = ["DNS:localhost", "IP:127.0.0.1"] + ([f"IP:{ip}"] if ip else [])
    return [
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365",
        "-subj", "/CN=localhost", "-addext", "subjectAltName=" + ",".join(names),
        "-keyout", key, "-out", cert,
    ]  # fmt: skip


def cert_task(force: bool = False) -> Path:
    certs = mosprom_home() / "certs"
    cert, key = certs / "cert.pem", certs / "key.pem"
    if cert.exists() and key.exists() and not force:
        say(f"OK: сертификат уже есть: {cert}")
        return cert
    need("openssl")
    certs.mkdir(parents=True, exist_ok=True)
    ip = laptop_ip()
    run(cert_command(cert, key, ip))
    if not IS_WINDOWS:
        key.chmod(0o600)
    say(f"OK: сертификат на localhost и {ip or 'без IP (сеть не найдена)'}: {cert}")
    say("телефону мастера нужно принять его в браузере (или установить cert.pem как доверенный)")
    return cert


def worker_token() -> str:
    """WORKER_TOKEN из окружения или из ~/.mosprom/worker_token (создаётся один раз)."""
    given = os.environ.get("WORKER_TOKEN")
    if given:
        return given
    path = mosprom_home() / "worker_token"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        if not IS_WINDOWS:
            path.chmod(0o600)
    return path.read_text(encoding="utf-8").strip()


def resolve_device(device: str, cuda: bool) -> str:
    """auto -> gpu, если есть CUDA, иначе cpu; остальное как есть."""
    if device == "auto":
        return "gpu" if cuda else "cpu"
    return device


def has_cuda(python) -> bool:
    code = "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)"
    try:
        return subprocess.run([str(python), "-c", code], capture_output=True).returncode == 0
    except OSError:
        return False


def pose_rule_enabled(rules_text: str) -> bool:
    """В rules.yaml (не в комментарии) есть правило distress_signal."""
    return any(
        line.split("#")[0].strip().startswith("distress_signal:")
        for line in rules_text.splitlines()
    )


def worker_command(
    python,
    camera: str | None,
    video: str | None,
    backend_url: str,
    ppe_weights: str | None,
    device: str = "cpu",
    pose_model: str | None = None,
) -> list:
    """Команда воркера. video=None -> один воркер с камерами из бэкенда.
    Файлам --loop (иначе ролик кончится), потокам (есть "://") — нет."""
    cmd = [python, "-m", "worker.main"]
    if video is None:
        cmd += ["--cameras-from-backend"]
    else:
        cmd += ["--camera", camera, "--video", video]
        if "://" not in video:
            cmd += ["--loop"]
    cmd += ["--backend", backend_url, "--device", device]
    if ppe_weights:
        cmd += ["--ppe-weights", ppe_weights]
    if pose_model:
        cmd += ["--pose-model", pose_model]
    return cmd


MAX_RESTARTS = 5
RESTART_PAUSE = 3.0


def supervise(procs: list, restart, sleep=time.sleep, max_restarts: int = MAX_RESTARTS) -> None:
    """Вечно следит за воркерами: упавший перезапускается (до max_restarts раз)."""
    counts = [0] * len(procs)
    while True:
        for i, proc in enumerate(procs):
            if proc.poll() is None:
                continue
            if counts[i] >= max_restarts:
                raise TaskError(f"воркер {i + 1} упал {max_restarts + 1} раз — смотри вывод выше")
            counts[i] += 1
            say(f"воркер {i + 1} завершился, перезапуск {counts[i]}/{max_restarts}")
            sleep(RESTART_PAUSE)
            procs[i] = restart(i)
        sleep(1)


def no_docker_commands(
    backend_python, npm, port: str, cert: Path, key: Path
) -> tuple[list, list, dict]:
    """Бэкенд (uvicorn на 127.0.0.1) и веб (vite preview, HTTPS 8443) без Docker.
    Третье — переменные окружения веба (сертификат, адрес бэкенда)."""
    uvicorn = [backend_python, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1"]
    uvicorn += ["--port", port]
    preview = [npm, "run", "preview", "--", "--host", "0.0.0.0", "--port", "8443"]
    web_env = {
        "MOSPROM_CERT": str(cert),
        "MOSPROM_KEY": str(cert.with_name("key.pem")),
        "VITE_API_TARGET": f"http://127.0.0.1:{port}",
    }
    return uvicorn, preview, web_env


def run_no_docker(
    videos: list[str], ppe_weights: str | None, device: str, from_backend: bool
) -> None:
    ml_python = ensure_env(ML)
    backend_python = ensure_env(BACKEND)
    npm = ensure_web_deps()
    cert = cert_task()
    token = worker_token()
    home = mosprom_home()
    data = home / "data"
    data.mkdir(parents=True, exist_ok=True)
    port = os.environ.get("PORT", "8080")
    https_url = "https://localhost:8443"
    backend_env = child_env(
        WORKER_TOKEN=token,
        MOSPROM_USERS=str(data / "users.json"),
        MOSPROM_DB=str(data / "mosprom.db"),
        MOSPROM_SNAPSHOTS=str(data / "snapshots"),
    )
    worker_env = child_env(WORKER_TOKEN=token, SSL_CERT_FILE=str(cert))
    uvicorn, preview, web_vars = no_docker_commands(backend_python, npm, port, cert, cert)
    procs: list[subprocess.Popen] = []
    try:
        ensure_port_free(port)
        ensure_port_free("8443")
        run([npm, "run", "build"], cwd=WEB)
        procs.append(start(uvicorn, BACKEND, backend_env))
        wait_http(f"http://127.0.0.1:{port}/healthz", timeout=60, proc=procs[0])
        procs.append(start(preview, WEB, child_env(**web_vars)))
        wait_https(f"{https_url}/api/events?limit=1", cert, timeout=60)
        cmds = worker_commands(ml_python, videos, from_backend, https_url, ppe_weights, device)
        workers = [start(cmd, ML, worker_env) for cmd in cmds]
        procs.extend(workers)
        ip = laptop_ip() or "<IP ноутбука>"
        say(f"\nбез Docker: https://{ip}:8443 (вход включён) — Ctrl+C остановит всё")
        say("заголовки безопасности nginx (HSTS, CSP ...) в этом режиме не ставятся")
        supervise(workers, lambda i: start(cmds[i], ML, worker_env))
    except KeyboardInterrupt:
        say("\nостанавливаю...")
    finally:
        for proc in reversed(procs):
            stop(proc)


def worker_commands(
    ml_python, videos: list[str], from_backend: bool, url: str, ppe_weights, device: str
) -> list[list]:
    device = resolve_device(device, has_cuda(ml_python) if device == "auto" else False)
    rules = ML / "worker" / "rules.yaml"
    pose = None
    if rules.exists() and pose_rule_enabled(rules.read_text(encoding="utf-8")):
        pose = "yolo11n-pose.pt"
    # воркер стартует из ml/: относительные пути файлов считаем от папки, где запустили tasks.py
    if ppe_weights:
        ppe_weights = str(Path(ppe_weights).resolve())
    if from_backend:
        return [worker_command(ml_python, None, None, url, ppe_weights, device, pose)]
    return [
        worker_command(
            ml_python,
            f"cam-{n}",
            video if "://" in video or video.isdigit() else str(Path(video).resolve()),
            url,
            ppe_weights,
            device,
            pose,
        )
        for n, video in enumerate(videos, 1)
    ]


def run_all(
    videos: list[str],
    ppe_weights: str | None,
    device: str = "auto",
    from_backend: bool = False,
    no_docker: bool = False,
) -> None:
    if not videos and not from_backend:
        raise TaskError(
            f"нужен --video или --from-backend: {PY} tasks.py run --video <файл или RTSP>"
        )
    if no_docker:
        return run_no_docker(videos, ppe_weights, device, from_backend)
    docker = need("docker")
    ml_python = ensure_env(ML)
    cert = cert_task()
    token = worker_token()
    home = mosprom_home()
    (home / "data").mkdir(parents=True, exist_ok=True)
    env = child_env(WORKER_TOKEN=token, MOSPROM_HOME=str(home))
    https_url = "https://localhost:8443"
    # Воркер доверяет только нашему сертификату (urllib читает SSL_CERT_FILE)
    worker_env = child_env(WORKER_TOKEN=token, SSL_CERT_FILE=str(cert))
    procs: list[subprocess.Popen] = []
    try:
        run([docker, "compose", "up", "-d", "--build", "backend", "web"], env=env)
        wait_https(f"{https_url}/api/events?limit=1", cert, timeout=120)
        cmds = worker_commands(ml_python, videos, from_backend, https_url, ppe_weights, device)
        procs.extend(start(cmd, ML, worker_env) for cmd in cmds)
        say(f"\nсистема запущена: {https_url} (вход включён) — Ctrl+C остановит всё")
        say(
            f"пользователь: {PY} -m app.cli add-user <login> <role> (в backend/, MOSPROM_USERS="
            f"{home / 'data' / 'users.json'})"
        )
        supervise(procs, lambda i: start(cmds[i], ML, worker_env))
    except KeyboardInterrupt:
        say("\nостанавливаю...")
    finally:
        for proc in reversed(procs):
            stop(proc)
        subprocess.run([docker, "compose", "stop", "web", "backend"], cwd=ROOT, env=env)


def wait_https(url: str, cert: Path, timeout: float) -> None:
    """Ждём, пока nginx ответит по HTTPS; 401 тоже ответ (вход включён)."""
    context = ssl.create_default_context(cafile=str(cert))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=3, context=context)
            return
        except urllib.error.HTTPError:
            return
        except (OSError, ssl.SSLError):
            time.sleep(1)
    raise TaskError(f"{url} не ответил за {timeout:.0f} с: docker compose logs web backend")


def e2e() -> None:
    port = "18080"
    url = f"http://127.0.0.1:{port}"
    sample = json.loads((DEMO / "events.sample.json").read_text(encoding="utf-8"))
    log = None if VERBOSE else Path(tempfile.gettempdir()) / "mosprom-e2e-backend.log"
    # своя база: в общей ~/.mosprom лежат события прошлых демо с чужими статусами
    scratch = Path(tempfile.mkdtemp(prefix="mosprom-e2e-"))
    env = child_env(MOSPROM_DB=str(scratch / "db.sqlite"), MOSPROM_SNAPSHOTS=str(scratch / "snaps"))
    # Бэкенд из venv гейта, мок — голым Python без site-packages: как в CI
    backend = start_backend(port, env, log, gate_env(BACKEND))
    try:
        wait_http(f"{url}/healthz", timeout=60, proc=backend)
        replay = [sys.executable, "-I", "-S", DEMO / "replay_events.py", "--backend", url]
        replay += ["--interval", "0"]
        run(replay, env=child_env())
        events = get_json(f"{url}/api/events?limit=1000")
        if len(events) < len(sample):
            raise TaskError(f"в API {len(events)} событий, ожидалось {len(sample)}")
        if not all(event.get("id") and event.get("status") == "new" for event in events):
            raise TaskError("у событий из API нет id или статус не new")
        say(f"OK: e2e — {len(events)} событий прошли через бэкенд")
        run([sys.executable, "-I", ROOT / "tests" / "e2e_dedup.py", url], env=child_env())
        e2e_auth()
    except TaskError:
        if log is not None and log.exists():
            say("лог бэкенда:\n" + tail(log.read_text(encoding="utf-8", errors="replace"), 30))
        raise
    finally:
        stop(backend)
        shutil.rmtree(scratch, ignore_errors=True)


def e2e_auth() -> None:
    """Второй бэкенд — с включённым входом и токеном воркера (P8)."""
    port = "18081"
    url = f"http://127.0.0.1:{port}"
    token = "e2e-worker-token"
    log = None if VERBOSE else Path(tempfile.gettempdir()) / "mosprom-e2e-auth-backend.log"
    script = ROOT / "tests" / "e2e_auth.py"
    with tempfile.TemporaryDirectory() as tmp:
        users = Path(tmp) / "users.json"
        run([sys.executable, "-I", script, "prepare", users], env=child_env())
        env = child_env(
            WORKER_TOKEN=token,
            MOSPROM_USERS=str(users), MOSPROM_DB=str(Path(tmp) / "db.sqlite"),
            MOSPROM_SNAPSHOTS=str(Path(tmp) / "snaps"),
        )  # fmt: skip
        backend = start_backend(port, env, log, gate_env(BACKEND))
        try:
            wait_http(f"{url}/healthz", timeout=60, proc=backend)
            run([sys.executable, "-I", script, "check", url, token], env=child_env())
        except TaskError:
            if log is not None and log.exists():
                say("лог бэкенда:\n" + tail(log.read_text(encoding="utf-8", errors="replace"), 30))
            raise
        finally:
            stop(backend)


def setup() -> None:
    ensure_env(ML)
    ensure_env(BACKEND)
    if shutil.which("npm"):
        ensure_web_deps()
    else:
        say(f"! нет npm — gate-web и demo не заработают: {INSTALL_HINTS['npm']}")
    say("\nготово. Необязательно: git config core.hooksPath .githooks включает гейты перед push")


REMOTE_MAIN = "refs/remotes/origin/main"  # с чем сравнивать при gate-changed


def linq_task(name: str) -> None:
    sys.path.insert(0, str(ROOT))
    from linq import up as linq_up

    if name != "linq-check" and not linq_up.is_admin():
        linq_up.relaunch_as_admin(name)
        return
    if name == "linq-down":
        linq_up.down()
        return
    try:
        if name == "linq-watch":
            try:
                linq_up.up(start_vpn=True, upload=False)
            except (SystemExit, Exception) as err:  # noqa: BLE001 - наблюдатель сам повторит
                print(f"первое подключение не удалось: {err}", flush=True)
            print("__DONE__", flush=True)  # метка для окна, которое ждёт запуска; сам остаюсь
            linq_up.watch()
            return
        linq_up.up(start_vpn=name in ("linq-up", "linq-watch"), upload=name == "linq-up")
    except ModuleNotFoundError as err:
        raise TaskError(f"{err}: pip install websocket-client") from err
    except Exception as err:  # JupyterError, нет профиля и т.п. - одной строкой, без трейса
        raise TaskError(str(err)) from err


LINQ_PASSTHROUGH = {
    "linq-build": "build_program",  # новые веса -> .tpu на стенде -> проверка качества
    "linq-doctor": "doctor",  # что ещё не настроено для подключения на этом компьютере
    "linq-edge": "edge_ctl",  # сервис кадров на стенде: start|stop|status
    "linq-live": "edge_ctl",  # живой поток на LinQ: сервис + воркер
}


def linq_passthrough(task: str, rest: list[str]) -> int:
    """Эти задачи разбирают свои флаги сами (у каждой они свои), tasks.py их не знает."""
    sys.path.insert(0, str(ROOT))
    import importlib

    module = importlib.import_module(f"linq.{LINQ_PASSTHROUGH[task]}")
    if task == "linq-live":
        rest = ["live", *rest]
    try:
        return module.main(rest)
    except ModuleNotFoundError as err:
        raise TaskError(f"{err}: pip install websocket-client") from err
    except SystemExit as err:
        if isinstance(err.code, str):
            raise TaskError(err.code) from err
        return int(err.code or 0)


TASKS = {
    "setup": setup,
    "linq-up": lambda: linq_task("linq-up"),
    "linq-check": lambda: linq_task("linq-check"),
    "linq-down": lambda: linq_task("linq-down"),
    "linq-watch": lambda: linq_task("linq-watch"),
    "gate-ml": lambda: run_gates(["ml"]),
    "gate-back": lambda: run_gates(["back"]),
    "gate-web": lambda: run_gates(["web"]),
    "gate": lambda: run_gates(list(GATES)),
    "gate-changed": gate_changed,
    "e2e": e2e,
    "cert": cert_task,
}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    raw = sys.argv[1:] if argv is None else argv
    if raw and raw[0] in LINQ_PASSTHROUGH:
        try:
            return linq_passthrough(raw[0], raw[1:])
        except TaskError as err:
            print(f"СТОП: {err}", file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(
        description="Задачи репозитория Zorky",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("task", choices=[*TASKS, *LINQ_PASSTHROUGH, "demo", "run"])
    parser.add_argument(
        "--video", action="append", default=[], help="run: файл или RTSP, повторяется"
    )
    parser.add_argument("--ppe-weights", help="run: веса СИЗ для воркеров")
    parser.add_argument(
        "--device", choices=["auto", "cpu", "gpu", "linq"], default="auto", help="run: устройство"
    )
    parser.add_argument(
        "--from-backend", action="store_true", help="run: камеры из бэкенда (заводит админ)"
    )
    parser.add_argument("--no-docker", action="store_true", help="run: без Docker (uvicorn + vite)")
    parser.add_argument("--no-mock", action="store_true", help="demo без мок-событий")
    parser.add_argument("--login", action="store_true", help="demo со входом: оператор и мастер")
    parser.add_argument("--dev", action="store_true", help="demo: dev-сервер вместо готовой сборки")
    args = parser.parse_args(argv)
    try:
        if args.task == "demo":
            demo(mock=not args.no_mock, login=args.login, dev=args.dev)
        elif args.task == "run":
            run_all(args.video, args.ppe_weights, args.device, args.from_backend, args.no_docker)
        else:
            TASKS[args.task]()
    except TaskError as err:
        say(f"\nОШИБКА: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
