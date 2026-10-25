"""P7: nginx.conf отдаёт HTTPS с заголовками безопасности, HTTP уходит в редирект; cert."""

import re
import subprocess
from pathlib import Path

import pytest

import tasks

CONF = (Path(__file__).resolve().parent.parent / "web" / "nginx.conf").read_text(encoding="utf-8")


def servers() -> list[str]:
    parts = re.split(r"^server \{", CONF, flags=re.M)[1:]
    assert len(parts) == 2
    return parts


def test_http_redirects_to_https_8443():
    http = next(s for s in servers() if "listen 80;" in s)
    assert re.search(r"return 301 https://\$host:8443\$request_uri;", http)


def test_https_server_listens_with_cert():
    https = next(s for s in servers() if "listen 443 ssl;" in s)
    assert "ssl_certificate /certs/cert.pem;" in https
    assert "ssl_certificate_key /certs/key.pem;" in https


@pytest.mark.parametrize(
    "name, value",
    [
        ("Strict-Transport-Security", "max-age="),
        ("Content-Security-Policy", "default-src 'self'"),
        ("X-Frame-Options", "DENY"),
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "no-referrer"),
    ],
)
def test_security_header_set_on_server_level(name, value):
    https = next(s for s in servers() if "listen 443 ssl;" in s)
    line = re.search(rf'^\s*add_header {name} "([^"]*)" always;', https, re.M)
    assert line, f"нет {name} (с always)"
    assert value in line.group(1)


def test_csp_images_only_self_and_blob():
    assert "img-src 'self' blob:" in CONF


def test_no_add_header_inside_locations():
    # add_header в location сбрасывает заголовки server-уровня
    for block in re.findall(r"location [^{]+\{([^}]*)\}", CONF):
        assert "add_header" not in block


def test_cert_command_has_localhost_and_ip(tmp_path):
    cmd = tasks.cert_command(tmp_path / "c.pem", tmp_path / "k.pem", "192.168.1.5")
    san = cmd[cmd.index("-addext") + 1]
    assert "DNS:localhost" in san and "IP:192.168.1.5" in san


def test_cert_task_writes_outside_git(tmp_path, monkeypatch):
    monkeypatch.setenv("MOSPROM_HOME", str(tmp_path))
    cert = tasks.cert_task()
    assert cert == tmp_path / "certs" / "cert.pem" and cert.exists()
    text = subprocess.run(
        ["openssl", "x509", "-in", str(cert), "-noout", "-ext", "subjectAltName"],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    assert "localhost" in text and "127.0.0.1" in text


def test_replay_sends_bearer_from_worker_token(monkeypatch):
    import importlib.util
    import urllib.request
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "replay_events", Path(__file__).resolve().parents[1] / "demo" / "replay_events.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def fake(request, timeout=0):
        seen["auth"] = request.get_header("Authorization")
        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setenv("WORKER_TOKEN", "abc")
    mod.post_event("http://x", {})
    assert seen["auth"] == "Bearer abc"


def test_no_docker_commands_pure(tmp_path):
    cert = tmp_path / "cert.pem"
    backend, web, env = tasks.no_docker_commands("py", "npm", "8090", cert, cert)
    assert backend[:4] == ["py", "-m", "uvicorn", "app.main:app"]
    assert backend[4:] == ["--host", "127.0.0.1", "--port", "8090"]
    assert web == ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "8443"]
    assert env["MOSPROM_CERT"] == str(cert)
    assert env["MOSPROM_KEY"] == str(tmp_path / "key.pem")
    assert env["VITE_API_TARGET"] == "http://127.0.0.1:8090"
