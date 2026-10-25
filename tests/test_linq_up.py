"""Автоподключение к LinQ: настройки, профиль OpenVPN, клиент Jupyter (без сети)."""

import base64
import json
import urllib.error
from pathlib import Path

import pytest

from linq import config, vpn
from linq.jupyter import Jupyter, JupyterError
from linq.stand_check import check
from linq.up import describe, push_programs


def test_env_file_parsing(tmp_path):
    env = tmp_path / "x"
    env.write_text('# c\nA=1\nB = "два"\n\nпусто\nC=\n', encoding="utf-8")
    assert config.read_env_file(env) == {"A": "1", "B": "два", "C": ""}
    assert config.read_env_file(tmp_path / "нет") == {}


def test_settings_defaults_and_priority(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "find_profile", lambda *_: "")
    file = tmp_path / "env"
    file.write_text("JUPYTER_LOGIN=из-файла\nJUPYTER_PASS=p\n", encoding="utf-8")
    s = config.load(env={"JUPYTER_LOGIN": "из-окружения"}, env_file=file)
    assert s.jupyter_login == "из-окружения"  # окружение процесса важнее файла
    assert s.jupyter_pass == "p"
    assert s.host == "10.0.114.30" and s.jupyter_url == "http://10.0.114.30"
    assert s.programs_dir == "/auto/cache/из-окружения"
    assert s.missing("ovpn_profile", "jupyter_login") == ["OVPN_PROFILE"]


def test_profile_is_split_tunnel(tmp_path):
    original = "client\nremote 1.2.3.4 1194\nredirect-gateway def1\nauth-user-pass\nroute-nopull\n"
    out = vpn.prepare_profile(original, "10.0.114.0 255.255.255.0", tmp_path / "a.txt")
    assert "redirect-gateway" not in out
    assert out.count("route-nopull") == 1
    assert 'pull-filter ignore "dhcp-option"' in out
    assert "route 10.0.114.0 255.255.255.0" in out
    assert "route 10.0.114.0 255.255.255.128" in out  # узкие половины побеждают чужой /24
    assert "route 10.0.114.128 255.255.255.128" in out
    assert out.count("auth-user-pass") == 1 and "a.txt" in out
    assert "remote 1.2.3.4 1194" in out  # настоящие директивы остались


def test_profile_uses_wintun_only_on_windows():
    assert "windows-driver wintun" in vpn.prepare_profile(
        "client", "10.0.0.0 255.0.0.0", windows=True
    )
    assert "windows-driver" not in vpn.prepare_profile(
        "client", "10.0.0.0 255.0.0.0", windows=False
    )


def test_find_openvpn(tmp_path, monkeypatch):
    exe = tmp_path / "openvpn.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(vpn.shutil, "which", lambda _name: None)
    monkeypatch.setattr(vpn, "WINDOWS_EXE", ())
    assert vpn.find_openvpn(str(exe)) == str(exe)
    assert vpn.find_openvpn("") == ""


def test_find_profile_takes_newest(tmp_path):
    import os

    old, new = tmp_path / "a.ovpn", tmp_path / "sub" / "b.ovpn"
    new.parent.mkdir()
    old.write_text("x")
    new.write_text("y")
    os.utime(old, (1, 1))
    assert config.find_profile([tmp_path]) == str(new)
    assert config.find_profile([tmp_path / "нет"]) == ""


def test_wait_reachable():
    calls = iter([False, False, True])
    assert vpn.wait_reachable("h", 1, 5, step=0, probe=lambda *_: next(calls))
    assert not vpn.wait_reachable("h", 1, 0, step=0, probe=lambda *_: False)


def test_prefer_interface_gives_up_on_hung_powershell(monkeypatch):
    seen = []

    def hung(cmd, **kwargs):
        seen.append(kwargs.get("timeout"))
        raise vpn.subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(vpn.sys, "platform", "win32")
    monkeypatch.setattr(vpn.subprocess, "run", hung)
    assert not vpn.prefer_interface(seconds=5)
    assert len(seen) == 1 and 0 < seen[0] <= 5


class Resp:
    def __init__(self, data=b"{}"):
        self.data = data

    def read(self):
        return self.data

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


class FakeOpener:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def open(self, request, timeout=0):
        key = (request.get_method(), request.selector)
        self.calls.append((key, request.data, dict(request.header_items())))
        if key not in self.routes:
            raise urllib.error.HTTPError(request.full_url, 404, "нет", {}, None)
        return Resp(json.dumps(self.routes[key]).encode())


def test_login_with_token_and_listdir():
    op = FakeOpener(
        {
            ("GET", "/api/status"): {},
            ("GET", "/api/contents/auto/cache/x"): {"content": [{"name": "b"}, {"name": "a"}]},
        }
    )
    jup = Jupyter("http://h", opener=op)
    jup.login(token="T")
    assert jup.listdir("/auto/cache/x") == ["a", "b"]
    assert op.calls[0][2]["Authorization"] == "token T"


def test_login_needs_credentials():
    with pytest.raises(JupyterError):
        Jupyter("http://h", opener=FakeOpener({})).login()


def test_upload_is_base64_put(tmp_path):
    op = FakeOpener({("PUT", "/api/contents/auto/cache/x/m.tpu"): {}})
    jup = Jupyter("http://h", opener=op)
    f = tmp_path / "m.tpu"
    f.write_bytes(b"\x00\x01ab")
    assert jup.upload_file(f, "/auto/cache/x/") == "auto/cache/x/m.tpu"
    body = json.loads(op.calls[-1][1])
    assert body["format"] == "base64" and base64.b64decode(body["content"]) == b"\x00\x01ab"


def test_http_error_is_readable():
    jup = Jupyter("http://h", opener=FakeOpener({}))
    with pytest.raises(JupyterError, match="404"):
        jup.listdir("x")


class FakeWs:
    def __init__(self, mark_holder):
        self.sent, self.queue, self.holder = [], [], mark_holder

    def send(self, text):
        self.sent.append(text)
        marker = text.split("__END_")[1].split("_")[0]
        self.queue.append(json.dumps(["stdout", f"привет\r\n__END_{marker}_0__\r\n"]))

    def recv(self):
        return self.queue.pop(0)

    def close(self):
        pass


def test_terminal_run_returns_output_and_code():
    op = FakeOpener({("POST", "/api/terminals"): {"name": "1"}, ("DELETE", "/api/terminals/1"): {}})
    jup = Jupyter("http://h", opener=op)
    ws = FakeWs(None)
    code, out = jup.run("echo привет", timeout=5, connect=lambda *a, **k: ws)
    assert code == 0 and "привет" in out and "__END_" not in out


def test_push_programs_uploads_tpu_and_json(tmp_path):
    for name in ("a.tpu", "a.tpu.json", "readme.txt"):
        (tmp_path / name).write_bytes(b"x")
    sent = []

    class J:
        def upload_file(self, path, remote_dir):
            sent.append((path.name, remote_dir))

        def run(self, command, timeout=0):
            sent.append(("cmd", command))
            return 0, ""

    s = config.load(env={"JUPYTER_LOGIN": "u"}, env_file=tmp_path / "нет")
    assert push_programs(J(), s, tmp_path) == ["a.tpu", "a.tpu.json"]
    assert sent[0][1].endswith("programs")
    assert "cp -f" in sent[-1][1] and "/auto/cache/u" in sent[-1][1]


def test_stand_check_and_describe(tmp_path, monkeypatch):
    report = check("нет-такого-логина", host="127.0.0.1", port=1)
    assert report["cache_exists"] is False and report["broker"] is False
    assert "НЕ готов" in describe(report)
    ready = {
        "cache_dir": "d",
        "programs": ["a.tpu"],
        "broker": True,
        "modules": {"pika": True, "tpu_pycloud_format": True},
    }
    assert "ГОТОВ" in describe(ready)
    assert isinstance(Path(__file__), Path)


def test_profile_survives_reconnects():
    out = vpn.prepare_profile("client\nremote h 1194", "10.0.114.0 255.255.255.0", None)
    for line in (
        "persist-tun",
        "resolv-retry infinite",
        "connect-retry 5 30",
    ):
        assert line in out.splitlines()


def test_wait_initialized_states(tmp_path):
    log = tmp_path / "openvpn.log"
    quick = {"step": 0, "sleep": lambda _s: None}
    assert vpn.wait_initialized(log, 0, **quick) == "timeout"  # журнала ещё нет
    log.write_text("x\nInitialization Sequence Completed\n", encoding="utf-8")
    assert vpn.wait_initialized(log, 5, **quick) == "ready"
    log.write_text("AUTH_FAILED", encoding="utf-8")
    assert vpn.wait_initialized(log, 5, **quick) == "failed"
    log.write_text("TLS: Initial packet", encoding="utf-8")
    assert vpn.wait_initialized(log, 5, alive=lambda: False, **quick) == "exited"


def test_auth_file_is_kept_while_handshake_is_pending(tmp_path, monkeypatch):
    """Регресс: файл логина удалялся через 60 с, а OpenVPN читал его позже и падал."""
    from linq import up as linq_up

    work = tmp_path / "w"
    work.mkdir()
    (work / "auth.txt").write_text("u\np\n", encoding="utf-8")
    (work / "openvpn.log").write_text("TLS: Initial packet", encoding="utf-8")

    class Proc:
        pid = 4242

        def poll(self):
            return None

    monkeypatch.setattr(linq_up, "STATE", tmp_path / "pid")
    monkeypatch.setattr(linq_up, "RUN_DIR", tmp_path)
    monkeypatch.setattr(linq_up, "HANDSHAKE_S", 0)
    monkeypatch.setattr(linq_up.vpn, "stand_visible", lambda _h: False)
    monkeypatch.setattr(linq_up.vpn, "start", lambda _s, _d: (Proc(), work))
    monkeypatch.setattr(linq_up.vpn, "blocking_filters", lambda: [])
    settings = config.load(
        {"OVPN_PROFILE": "p", "OVPN_USER": "u", "OVPN_PASS": "p", "LINQ_USER": "l"}
    )
    with pytest.raises(SystemExit):
        linq_up.ensure_vpn(settings)
    assert (work / "auth.txt").exists()  # рукопожатие не закончилось: файл нужен


def test_watch_restarts_tunnel_after_consecutive_failures(tmp_path, monkeypatch):
    from linq import up as linq_up

    monkeypatch.setattr(linq_up, "WATCH_STOP", tmp_path / "stop")
    seen = iter([True, False, False, False, True])
    restarts = []
    linq_up.watch(
        config.load({}),
        interval=0,
        fails=3,
        rounds=5,
        sleep=lambda _s: None,
        ensure=lambda _s: restarts.append(1),
        visible=lambda: next(seen),
    )
    assert restarts == [1]


def test_watch_stops_on_marker(tmp_path, monkeypatch):
    from linq import up as linq_up

    marker = tmp_path / "stop"
    monkeypatch.setattr(linq_up, "WATCH_STOP", marker)
    calls = []

    def visible():
        calls.append(1)
        marker.write_text("stop", encoding="utf-8")
        return True

    linq_up.watch(config.load({}), interval=0, rounds=10, sleep=lambda _s: None, visible=visible)
    assert len(calls) == 1


def test_cleanup_can_keep_files_for_the_tunnel_lifetime(tmp_path):
    """Регресс: через час OpenVPN меняет ключи и снова читает файл логина - он должен быть."""
    (tmp_path / "auth.txt").write_text("u\np\n", encoding="utf-8")
    (tmp_path / "client.ovpn").write_text("client", encoding="utf-8")
    vpn.cleanup(tmp_path, keep_auth=True)
    assert (tmp_path / "auth.txt").exists() and (tmp_path / "client.ovpn").exists()
    vpn.cleanup(tmp_path)
    assert not (tmp_path / "auth.txt").exists()


def test_profile_drops_auth_nocache():
    out = vpn.prepare_profile("client\nauth-nocache\nremote h 1194", "10.0.114.0 255.255.255.0")
    assert "auth-nocache" not in out
