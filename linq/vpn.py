"""OpenVPN хакатона рядом с другим VPN (Amnezia): маршрут только на подсеть стенда."""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .config import Settings

# Строки, которые делают профиль «вежливым»: не перехватывать маршрут по умолчанию и DNS.
SPLIT_LINES = (
    "route-nopull",
    'pull-filter ignore "dhcp-option"',
    'pull-filter ignore "block-outside-dns"',
)


# Переживать обрывы: не рвать туннель при сбое сети, повторять подключение без конца.
RESILIENCE_LINES = (
    "persist-tun",
    "resolv-retry infinite",
    "connect-retry 5 30",
    "connect-timeout 20",
)

READY_MARK = "Initialization Sequence Completed"
FAILED_MARKS = ("AUTH_FAILED", "Exiting due to fatal error")

WINDOWS_EXE = (
    "C:/Program Files/OpenVPN/bin/openvpn.exe",
    "C:/Program Files (x86)/OpenVPN/bin/openvpn.exe",
)


def halves(subnet: str) -> list[str]:
    """«10.0.114.0 255.255.255.0» -> два маршрута вдвое уже («… 255.255.255.128»).

    Более узкий маршрут побеждает при равной метрике: так трафик до стенда идёт через этот VPN,
    даже если другой VPN (Amnezia) держит маршрут на всю подсеть.
    """
    net = ipaddress.ip_network(subnet.replace(" ", "/"), strict=False)
    if net.prefixlen >= 30:
        return [f"{net.network_address} {net.netmask}"]
    return [f"{half.network_address} {half.netmask}" for half in net.subnets(prefixlen_diff=1)]


def find_openvpn(configured: str = "") -> str:
    """openvpn.exe: из OVPN_EXE, иначе из PATH или обычных мест установки. Пусто — не нашли."""
    candidates = [configured, shutil.which("openvpn") or "", *WINDOWS_EXE]
    return next((c for c in candidates if c and Path(c).is_file()), "")


def prepare_profile(
    text: str,
    subnet: str,
    auth_file: Path | None = None,
    windows: bool = sys.platform == "win32",
) -> str:
    """Копия профиля: раздельная маршрутизация и (если задан) файл логина вместо вопроса в консоли.

    Настоящие директивы профиля остаются; чужие `redirect-gateway` и `auth-user-pass` убираем.
    """
    kept = []
    for line in text.splitlines():
        word = line.strip().split(" ", 1)[0].lower()
        # auth-nocache заставил бы читать файл логина заново при каждой смене ключей
        if word in ("redirect-gateway", "auth-user-pass", "route-nopull", "auth-nocache"):
            continue
        if line.strip().lower().startswith("pull-filter ignore") and "dhcp-option" in line:
            continue
        if line.strip().lower().startswith("pull-filter ignore") and "block-outside-dns" in line:
            continue
        kept.append(line)
    # wintun: свой адаптер, не занимает TAP, которым может пользоваться другой VPN
    extra = [
        *SPLIT_LINES,
        *RESILIENCE_LINES,
        *(["windows-driver wintun"] if windows else []),
        *(f"route {r}" for r in [subnet, *halves(subnet)]),
    ]
    if auth_file is not None:
        extra.append(f'auth-user-pass "{auth_file.as_posix()}"')
    return "\n".join(
        [*kept, "", "# --- mosprom: только трафик до стенда через этот VPN ---", *extra, ""]
    )


def reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


STAND_PORTS = (80, 5672, 8888)  # Jupyter и брокер: ответил любой — стенд виден


def stand_visible(host: str, probe=reachable) -> bool:
    return any(probe(host, port) for port in STAND_PORTS)


def wait_reachable(
    host: str, port: int, seconds: float, step: float = 2.0, probe=reachable
) -> bool:
    """Ждёт, пока host:port ответит; False — не дождались."""
    deadline = time.monotonic() + seconds
    while True:
        if probe(host, port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(step)


PREFER_PS = (
    "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -like '%s' } | "
    "ForEach-Object { Set-NetIPInterface -InterfaceIndex $_.InterfaceIndex -InterfaceMetric 1; "
    "'metric-set' }"
)


def prefer_interface(vpn_ip_mask: str = "10.0.113.*", seconds: float = 40.0) -> bool:
    """Windows: метрика адаптера VPN хакатона = 1, чтобы его маршруты побеждали чужой VPN.

    Другой VPN (Amnezia) держит такие же маршруты на подсеть стенда с меньшей суммарной метрикой
    (метрика адаптера 5 против 25 у TAP). Нужны права администратора. True - метрика выставлена.
    """
    if sys.platform != "win32":
        return False
    deadline = time.monotonic() + seconds
    while (left := deadline - time.monotonic()) > 0:
        try:  # без таймаута зависший PowerShell держал linq-up бесконечно
            done = subprocess.run(
                ["powershell", "-NoProfile", "-Command", PREFER_PS % vpn_ip_mask],
                capture_output=True,
                text=True,
                timeout=max(left, 1.0),
            )
        except subprocess.TimeoutExpired:
            return False
        if "metric-set" in done.stdout:
            return True
        time.sleep(2)
    return False


def blocking_filters(limit: int = 12) -> list[str]:
    """Windows: имена фильтров брандмауэра (WFP), похожих на kill switch другого VPN.

    «Отказано в доступе к сокету» (WSAEACCES) при подключении к стенду значит, что трафик режет
    фильтр, а не маршрут. Нужны права администратора; вне Windows - пусто.
    """
    if sys.platform != "win32":
        return []
    out = Path(tempfile.gettempdir()) / "mosprom-wfp.xml"
    try:
        subprocess.run(
            ["netsh", "wfp", "show", "filters", f"file={out}"], capture_output=True, timeout=30
        )
    except subprocess.TimeoutExpired:
        return []
    if not out.is_file():
        return []
    raw = out.read_bytes()
    text = raw.decode(
        "utf-16" if raw[:2] in (bytes([255, 254]), bytes([254, 255])) else "utf-8", "replace"
    )
    out.unlink(missing_ok=True)
    names = re.findall(r"<name>([^<]*)</name>", text)
    hits = [
        n
        for n in dict.fromkeys(names)
        if re.search(r"amnezia|kill|block all|wintun|wireguard", n, re.I)
    ]
    return hits[:limit]


def start(settings: Settings, workdir: Path | None = None) -> tuple[subprocess.Popen, Path]:
    """Запускает openvpn с нашей копией профиля. Возвращает (процесс, папка с временными файлами).

    Файл логина лежит во временной папке и удаляется в `cleanup`; в git и логи он не попадает.
    """
    exe = find_openvpn(settings.ovpn_exe if settings.ovpn_exe != "openvpn" else "")
    if not exe:
        raise FileNotFoundError(
            "нет openvpn.exe: поставь OpenVPN Community или впиши путь в OVPN_EXE"
        )
    profile = Path(settings.ovpn_profile)
    if not profile.is_file():
        raise FileNotFoundError(f"нет профиля OpenVPN: {profile}")
    work = workdir or Path(tempfile.mkdtemp(prefix="mosprom-ovpn-"))
    auth = work / "auth.txt"
    auth.write_text(f"{settings.ovpn_user}\n{settings.ovpn_pass}\n", encoding="utf-8")
    restrict_to_owner(auth)
    config = work / "client.ovpn"
    config.write_text(
        prepare_profile(profile.read_text(encoding="utf-8"), settings.subnet, auth),
        encoding="utf-8",
    )
    log = work / "openvpn.log"
    log.unlink(missing_ok=True)  # иначе старая строка «готов» принимается за новый запуск
    proc = subprocess.Popen(
        [exe, "--config", str(config), "--log", str(log)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc, work


def restrict_to_owner(path: Path) -> None:
    """Файл логина читает только владелец (Windows: icacls, иначе chmod 600)."""
    if sys.platform == "win32":
        user = os.environ.get("USERNAME", "")
        if user:
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(R,W,D)"],
                capture_output=True,
            )
        return
    try:
        path.chmod(0o600)
    except OSError:
        pass


def wait_initialized(
    log: Path, seconds: float, alive=lambda: True, step: float = 1.0, sleep=time.sleep
) -> str:
    """Ждёт конца рукопожатия OpenVPN по его журналу: `ready`, `failed`, `exited` или `timeout`.

    Файл логина нельзя удалять раньше: OpenVPN читает его уже после рукопожатия TLS, а на
    медленном канале оно идёт дольше минуты — удалённый файл убивал туннель («Error opening
    'Auth' auth file»).
    """
    deadline = time.monotonic() + seconds
    while True:
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        if READY_MARK in text:
            return "ready"
        if any(mark in text for mark in FAILED_MARKS):
            return "failed"
        if not alive():
            return "exited"
        if time.monotonic() >= deadline:
            return "timeout"
        sleep(step)


def pid_alive(pid: int) -> bool:
    """Жив ли процесс (Windows: tasklist, иначе kill 0)."""
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def kill_pid(pid: int) -> None:
    """Остановить прежний OpenVPN, чтобы два процесса не делили адаптер и маршруты."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def cleanup(work: Path, keep_auth: bool = False) -> None:
    """Убрать временные файлы. `keep_auth`: профиль и логин нужны OpenVPN при каждой смене ключей
    (раз в час), поэтому на время работы туннеля они остаются; удаляет их `linq-down`."""
    for name in () if keep_auth else ("auth.txt", "client.ovpn"):
        try:
            (work / name).unlink()
        except OSError:
            pass


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
