import json

from eval.feedback import export


def ev(i, status, typ, bbox, inc=None):
    return {
        "id": i,
        "incident_id": inc or i,
        "ts": "2026-10-21T10:00:00Z",
        "type": typ,
        "bbox": bbox,
        "status": status,
        "snapshot_url": f"/api/snapshots/{i}.jpg",
    }


def test_two_incidents(tmp_path):
    events = [
        ev("evt-1", "confirmed", "no_helmet", [0.2, 0.2, 0.4, 0.6]),
        ev("evt-2", "false", "smoking", [0.1, 0.1, 0.2, 0.3]),
        ev("evt-3", "new", "fall", [0.1, 0.1, 0.2, 0.3]),
    ]
    manifest = export(events, lambda url: url.encode(), tmp_path, {"no_helmet": 0, "smoking": 3})
    assert [m["verdict"] for m in manifest] == ["confirmed", "false"]
    assert (tmp_path / "images" / "evt-1.jpg").read_bytes() == b"/api/snapshots/evt-1.jpg"
    assert (tmp_path / "labels" / "evt-1.txt").read_text().split() == [
        "0", "0.300000", "0.400000", "0.200000", "0.400000",
    ]  # fmt: skip
    assert (tmp_path / "labels" / "evt-2.txt").read_text() == ""
    assert not (tmp_path / "images" / "evt-3.jpg").exists()
    saved = json.loads((tmp_path / "manifest.json").read_text())
    assert saved[1]["bbox"] == [0.1, 0.1, 0.2, 0.3] and saved[1]["type"] == "smoking"


def _server():
    import http.server
    import threading

    log = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body=b"[]", headers=()):
            self.send_response(code)
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers["Content-Length"])
            d = json.loads(self.rfile.read(n))
            log.append(("login", d))
            if d["password"] == "good":
                self._send(200, b"{}", [("Set-Cookie", "session=tok; Path=/")])
            else:
                self._send(401, b'{"error":"x"}')

        def do_GET(self):
            log.append(("get", self.headers.get("Cookie")))
            if self.headers.get("Cookie") == "session=tok":
                self._send(200)
            else:
                self._send(401, b'{"error":"login"}')

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}", log


def test_login_cookie_and_401():
    import pytest

    from eval.feedback import AuthError, fetch_events, make_opener

    srv, base, log = _server()
    try:
        op = make_opener(base, "aud", "good")
        assert fetch_events(base, op) == []
        assert log[0] == ("login", {"login": "aud", "password": "good"})
        assert all(c == "session=tok" for k, c in log[1:])
        with pytest.raises(AuthError) as bad:
            make_opener(base, "aud", "secret-bad")
        assert "secret-bad" not in str(bad.value)
        with pytest.raises(AuthError, match="требует вход"):
            fetch_events(base)  # без входа сервер отвечает 401
    finally:
        srv.shutdown()
