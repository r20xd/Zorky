"""Одна команда подключения к LinQ: VPN до стенда → вход в Jupyter → программы → проверка тракта."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import vpn
from .config import ROOT, Settings, load
from .jupyter import Jupyter, JupyterError, connect

PROGRAMS_LOCAL = ROOT / "ml" / "linq_programs"
STAND_DIR = "mosprom"  # папка для наших скриптов в корне Jupyter (на стенде это ~/work)
STAND_SHELL_DIR = "~/work/" + STAND_DIR  # она же в терминале
STATE = Path.home() / ".mosprom" / "linq_vpn.pid"
WATCH_STOP = Path.home() / ".mosprom" / "linq_watch.stop"  # `linq-down` просит наблюдателя уйти


def is_admin() -> bool:
    if sys.platform != "win32":
        return True
    import ctypes

    return bool(ctypes.windll.shell32.IsUserAnAdmin())


ELEVATED_LOG = ROOT / "linq_up.log"
RUN_DIR = ROOT / ".linq-run"  # временные файлы OpenVPN: читаемы и без прав администратора


def scheduled_task_exists(task: str) -> bool:
    if sys.platform != "win32":
        return False
    done = subprocess.run(["schtasks", "/query", "/tn", f"mosprom-{task}"], capture_output=True)
    return done.returncode == 0


def wait_for_log(wait: float) -> None:
    """Печатает вывод администраторского запуска из linq_up.log до метки __DONE__."""
    import time

    deadline = time.monotonic() + wait
    shown = 0
    while time.monotonic() < deadline:
        text = ELEVATED_LOG.read_text(encoding="utf-8", errors="replace")
        if len(text) > shown:
            say(text[shown:].replace("__DONE__", "").rstrip())
            shown = len(text)
        if "__DONE__" in text:
            return
        time.sleep(1)
    raise SystemExit(f"за {wait:.0f} с администраторское окно не закончило; смотри {ELEVATED_LOG}")


def relaunch_as_admin(task: str, wait: float = 240.0) -> None:
    """Windows: тот же `tasks.py <task>` скрытым окном с правами администратора (запрос UAC).

    OpenVPN без прав не может ни создать адаптер, ни добавить маршрут («Отказано в доступе»).
    Вывод пишется в linq_up.log; этот процесс ждёт метку конца и печатает вывод.
    """
    import ctypes

    ELEVATED_LOG.write_text("", encoding="utf-8")
    if scheduled_task_exists(task):  # задача с повышенными правами: без запроса UAC
        subprocess.run(["schtasks", "/run", "/tn", f"mosprom-{task}"], capture_output=True)
        return wait_for_log(wait)
    command = (
        f'cd /d "{ROOT}" && "{sys.executable}" tasks.py {task} > "{ELEVATED_LOG}" 2>&1 '
        f'& echo __DONE__ >> "{ELEVATED_LOG}"'
    )
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", "cmd.exe", f'/c "{command}"', None, 0
    )
    if result <= 32:
        raise SystemExit("права администратора не выданы — нужно подтвердить запрос Windows (UAC)")
    return wait_for_log(wait)


def say(text: str) -> None:
    print(text, flush=True)


def need(settings: Settings, *names: str) -> None:
    missing = settings.missing(*names)
    if missing:
        raise SystemExit("Заполни в .env: " + ", ".join(missing) + " (шаблон — .env.example)")


def push_programs(jupyter: Jupyter, settings: Settings, folder: Path = PROGRAMS_LOCAL) -> list[str]:
    """Загружает .tpu и .tpu.json из папки в /auto/cache/<логин>/ (то, что собрал build_tpu.sh)."""
    done = []
    staging = (
        f"{STAND_DIR}/programs"  # Jupyter видит только домашний каталог; в /auto/cache копируем
    )
    for path in sorted([*folder.glob("*.tpu"), *folder.glob("*.tpu.json")]):
        jupyter.upload_file(path, staging)
        done.append(path.name)
    if done:
        target = settings.programs_dir
        code, out = jupyter.run(
            f"mkdir -p {target} && cp -f {STAND_SHELL_DIR}/programs/* {target}/ && ls {target}"
        )
        if code != 0:
            raise JupyterError(f"не скопировал программы в {target}: {out[-200:]}")
    return done


def stand_report(jupyter: Jupyter, settings: Settings) -> dict:
    """Загружает stand_check.py на стенд, запускает его и возвращает отчёт."""
    script = Path(__file__).with_name("stand_check.py")
    jupyter.upload_file(script, STAND_DIR)
    code, out = jupyter.run(
        f"python3 {STAND_SHELL_DIR}/stand_check.py {settings.jupyter_login} "
        f"{settings.host} {settings.broker_port}",
        timeout=60,
    )
    line = next((x for x in out.splitlines() if x.startswith("STAND_CHECK ")), None)
    if code != 0 or line is None:
        raise JupyterError(f"проверка на стенде не удалась (код {code}): {out[-300:]}")
    return json.loads(line.removeprefix("STAND_CHECK "))


HANDSHAKE_S = 150  # рукопожатие на медленном канале бывает дольше минуты


def ensure_vpn(s: Settings) -> None:
    """Поднимает OpenVPN, если стенд не виден. Прежний (зависший) процесс останавливает.

    Файл логина удаляется только после рукопожатия (или смерти процесса), не по таймеру.
    """
    if vpn.stand_visible(s.host):
        return
    need(s, "ovpn_profile", "ovpn_user", "ovpn_pass")
    previous = STATE.read_text(encoding="utf-8").strip() if STATE.is_file() else ""
    if previous.isdigit() and vpn.pid_alive(int(previous)):
        say("прежний OpenVPN не даёт доступа к стенду, перезапускаю")
        vpn.kill_pid(int(previous))
    say("запускаю OpenVPN (нужны права администратора)…")
    RUN_DIR.mkdir(exist_ok=True)
    proc, work = vpn.start(s, RUN_DIR)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(str(proc.pid), encoding="utf-8")
    state = vpn.wait_initialized(
        work / "openvpn.log", HANDSHAKE_S, alive=lambda: proc.poll() is None
    )
    if state in ("failed", "exited"):
        vpn.cleanup(work)
    if state != "ready":
        say(f"рукопожатие OpenVPN: {state}; лог {work / 'openvpn.log'}")
        say(
            "похожие на kill switch фильтры брандмауэра: "
            + (", ".join(vpn.blocking_filters()) or "нет")
        )
        raise SystemExit(f"OpenVPN не поднялся ({state})")
    say("адаптер VPN: метрика 1" if vpn.prefer_interface() else "метрику адаптера не выставил")
    if not vpn.wait_reachable(s.host, 80, 60, probe=lambda h, _p: vpn.stand_visible(h)):
        raise SystemExit(f"туннель есть, но {s.host} не отвечает")
    vpn.cleanup(work, keep_auth=True)  # логин нужен при смене ключей; уберёт linq-down


def watch(
    settings: Settings | None = None,
    interval: float = 15.0,
    fails: int = 3,
    rounds: int | None = None,
    sleep=None,
    ensure=None,
    visible=None,
) -> None:
    """Держит туннель до стенда: при `fails` неудачных проверках подряд поднимает его заново.

    Работает под администратором (запускается `py tasks.py linq-watch`). `rounds` и подмены
    нужны тестам; в работе цикл бесконечный.
    """
    import time

    s = settings or load()
    sleep = sleep or time.sleep
    ensure = ensure or ensure_vpn
    visible = visible or (lambda: vpn.stand_visible(s.host))
    bad, done = 0, 0
    WATCH_STOP.unlink(missing_ok=True)
    say(f"слежу за туннелем до {s.host}: проверка раз в {interval:.0f} с")
    while rounds is None or done < rounds:
        if WATCH_STOP.exists():
            say("наблюдатель остановлен (linq-down)")
            return
        done += 1
        if visible():
            if bad >= fails:
                say("стенд снова виден")
            bad = 0
        else:
            bad += 1
            say(f"стенд не отвечает ({bad}/{fails})")
            if bad >= fails:
                try:
                    ensure(s)
                    bad = 0
                except SystemExit as exc:
                    say(f"перезапуск не удался: {exc}")
        sleep(interval)


def up(settings: Settings | None = None, start_vpn: bool = True, upload: bool = True) -> dict:
    """VPN (если нужен и не поднят) → Jupyter → .tpu → отчёт стенда. Возвращает отчёт."""
    s = settings or load()
    need(s, "jupyter_login")
    if not vpn.stand_visible(s.host):
        if not start_vpn:
            raise SystemExit(f"{s.host} недоступен: включи VPN хакатона или запусти без --no-vpn")
        ensure_vpn(s)
    say(f"стенд {s.host} доступен, вхожу в Jupyter как {s.jupyter_login}")
    jupyter = connect(s)
    if upload and PROGRAMS_LOCAL.is_dir():
        sent = push_programs(jupyter, s)
        say(
            f"загружено программ: {len(sent)}"
            if sent
            else "в ml/linq_programs нет .tpu — загрузка пропущена"
        )
    report = stand_report(jupyter, s)
    save_report(report)
    say(describe(report))
    return report


def is_ready(report: dict) -> bool:
    mods = report["modules"]
    return bool(
        report["broker"]
        and mods.get("pika")
        and mods.get("tpu_pycloud_format")
        and report["programs"]
    )


def save_report(report: dict, home: Path | None = None) -> Path:
    """Отчёт стенда для дашборда: ~/.mosprom/linq_report.json (без секретов)."""
    import time

    path = (home or Path.home() / ".mosprom") / "linq_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {**report, "ready": is_ready(report), "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def describe(report: dict) -> str:
    mods = report["modules"]
    lines = [
        f"программы в {report['cache_dir']}: {', '.join(report['programs']) or 'нет'}",
        f"брокер RabbitMQ: {'доступен' if report['broker'] else 'НЕДОСТУПЕН'}",
        "модули: " + ", ".join(f"{k}={'да' if v else 'нет'}" for k, v in mods.items()),
    ]
    lines.append(
        "LinQ ГОТОВ к работе" if is_ready(report) else "LinQ ещё НЕ готов — смотри строки выше"
    )
    return chr(10).join(lines)


def down() -> None:
    """Останавливает OpenVPN, который поднимал `up`."""
    import os
    import signal

    WATCH_STOP.parent.mkdir(parents=True, exist_ok=True)
    WATCH_STOP.write_text("stop", encoding="utf-8")  # иначе наблюдатель поднимет туннель обратно
    vpn.cleanup(RUN_DIR)  # файл логина жил, пока жил туннель
    if not STATE.is_file():
        say("OpenVPN от linq-up не запущен")
        return
    try:
        os.kill(int(STATE.read_text(encoding="utf-8")), signal.SIGTERM)
        say("OpenVPN остановлен")
    except (OSError, ValueError):
        say("процесс OpenVPN уже не работает")
    STATE.unlink(missing_ok=True)
