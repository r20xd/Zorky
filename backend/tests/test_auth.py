import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import cli
from app.application.auth_service import AuthService
from app.application.notifier import NopNotifier, WebNotifier
from app.domain import users
from app.infrastructure.memory_store import MemoryStore
from app.main import build_auth, create_app

EVENT = {
    "camera_id": "cam-1",
    "ts": "2026-10-21T10:00:00Z",
    "frame_id": 1,
    "type": "danger_zone",
    "bbox": [0.6, 0.3, 0.7, 0.9],
    "confidence": 0.9,
}
TOKEN = "worker-secret"


@pytest.fixture
def auth(tmp_path) -> AuthService:
    path = tmp_path / "users.json"
    for login, role in [
        ("ivan", "operator"),
        ("petr", "master"),
        ("sid", "specialist"),
        ("aud", "auditor"),
        ("adm", "admin"),
    ]:
        cli.add_user(path, login, role, login.title(), "pw-" + login)
    return build_auth(users_file=path, worker_token=TOKEN)


@pytest.fixture
def client(auth) -> TestClient:
    return TestClient(create_app(MemoryStore(), NopNotifier(), auth=auth))


def login(client: TestClient, name: str, password: str | None = None):
    return client.post(
        "/api/auth/login", json={"login": name, "password": password or "pw-" + name}
    )


def test_401_without_session(client):
    assert client.get("/api/events").status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/healthz").status_code == 200


def test_login_cookie_and_me(client, auth):
    r = login(client, "ivan")
    assert r.status_code == 200 and r.json() == {
        "login": "ivan",
        "name": "Ivan",
        "role": "operator",
    }
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" not in cookie
    assert client.get("/api/auth/me").json()["login"] == "ivan"
    assert client.get("/api/events").status_code == 200
    # в SQLite только хеш токена
    token = client.cookies.get("session")
    rows = sqlite3.Connection.execute(
        auth._sessions._db, "SELECT token_hash FROM sessions"
    ).fetchall()
    assert rows and all(token not in row[0] for row in rows)
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_secure_cookie_behind_https(client):
    r = client.post(
        "/api/auth/login",
        json={"login": "ivan", "password": "pw-ivan"},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert "secure" in r.headers["set-cookie"].lower()


def test_wrong_role_403(client):
    login(client, "ivan")
    assert client.put("/api/cameras/cam-1/zones", json=[]).status_code == 403
    login(client, "sid")
    assert client.put("/api/cameras/cam-1/zones", json=[]).status_code == 200
    assert client.patch("/api/events/x", json={"status": "false"}).status_code == 403


def test_worker_needs_token(client):
    assert client.post("/api/events", json=EVENT).status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert client.post("/api/events", json=EVENT, headers=bad).status_code == 401
    good = {"Authorization": f"Bearer {TOKEN}"}
    assert client.post("/api/events", json=EVENT, headers=good).status_code == 201
    assert client.get("/api/cameras/cam-1/zones", headers=good).status_code == 200
    assert client.get("/api/events", headers=good).status_code == 401  # журнал — людям
    login(client, "ivan")  # сессия человека не заменяет токен воркера
    assert client.post("/api/events", json=EVENT).status_code == 401


def test_by_comes_from_session(client):
    good = {"Authorization": f"Bearer {TOKEN}"}
    event = client.post("/api/events", json=EVENT, headers=good).json()
    login(client, "petr")
    r = client.patch(f"/api/events/{event['id']}", json={"status": "resolved", "by": "hacker"})
    assert r.status_code == 200
    assert r.json()["status_history"][-1]["by"] == "petr"


def test_bruteforce_429(client):
    for _ in range(5):
        assert login(client, "ivan", "bad").status_code == 401
    r = login(client, "ivan")  # даже верный пароль после 5 неверных
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert login(client, "petr").status_code == 200  # другой логин не задет


def test_foreign_origin_403(client):
    login(client, "ivan")
    r = client.patch(
        "/api/events/x", json={"status": "false"}, headers={"Origin": "http://evil.io"}
    )
    assert r.status_code == 403
    same = {"Origin": "http://testserver"}
    assert client.patch("/api/events/x", json={"status": "false"}, headers=same).status_code == 404


def test_dev_mode_open(tmp_path):
    auth = build_auth(users_file=tmp_path / "none.json", worker_token=None)
    c = TestClient(create_app(MemoryStore(), NopNotifier(), auth=auth))
    event = c.post("/api/events", json=EVENT).json()
    r = c.patch(f"/api/events/{event['id']}", json={"status": "confirmed", "by": "x"})
    assert r.json()["status_history"][-1]["by"] == "x"


def test_users_file_scrypt(tmp_path):
    path = tmp_path / "u.json"
    cli.add_user(path, "a", "manager", "A", "secret")
    record = json.loads(path.read_text(encoding="utf-8"))["a"]
    assert "secret" not in path.read_text(encoding="utf-8") and record["salt"]
    assert users.check_password(record, "secret") and not users.check_password(record, "x")


def test_cli_uses_getpass(tmp_path, monkeypatch):
    path = tmp_path / "u.json"
    monkeypatch.setenv("MOSPROM_USERS", str(path))
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "pw")
    assert cli.main(["add-user", "bob", "master", "--name", "Bob"]) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["bob"]["role"] == "master"


def test_auditor_reads_but_cannot_patch(client):
    good = {"Authorization": f"Bearer {TOKEN}"}
    event = client.post("/api/events", json=EVENT, headers=good).json()
    login(client, "aud")
    assert client.get("/api/events").status_code == 200
    assert client.get("/api/events.csv").status_code == 200
    r = client.patch(f"/api/events/{event['id']}", json={"status": "confirmed"})
    assert r.status_code == 403


def test_cameras_admin_only_and_source_hidden(client):
    cam = {"id": "c9", "name": "Цех", "source": "rtsp://secret/1"}
    login(client, "ivan")
    assert client.post("/api/cameras", json=cam).status_code == 403  # оператор камеры не заводит
    login(client, "adm")
    assert client.post("/api/cameras", json=cam).status_code == 201
    assert client.get("/api/cameras").json()[0]["source"] == "rtsp://secret/1"
    login(client, "ivan")
    assert "source" not in client.get("/api/cameras").json()[0]
    assert client.delete("/api/cameras/c9").status_code == 403
    good = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/api/cameras", headers=good).json()[0]["source"] == "rtsp://secret/1"
    login(client, "adm")
    assert client.delete("/api/cameras/c9").status_code == 204
    assert client.get("/api/cameras").json() == []


def test_reason_in_history_and_csv(client):
    good = {"Authorization": f"Bearer {TOKEN}"}
    event = client.post("/api/events", json=EVENT, headers=good).json()
    login(client, "ivan")
    url = f"/api/events/{event['id']}"
    assert client.patch(url, json={"status": "confirmed", "reason": "other"}).status_code == 400
    r = client.patch(url, json={"status": "false", "reason": "not_person"})
    assert r.status_code == 200 and r.json()["status_history"][-1]["reason"] == "not_person"
    assert client.patch(url, json={"status": "false", "reason": "bad"}).status_code == 400
    csv_text = client.get("/api/events.csv").text
    assert csv_text.splitlines()[0].split(",")[5] == "reason" and "not_person" in csv_text


def test_handover_signature_in_history(auth):
    notifier = WebNotifier()
    client = TestClient(create_app(MemoryStore(), notifier, auth=auth))
    notifier._hub.subscribe(master=True)  # мастер в сети
    good = {"Authorization": f"Bearer {TOKEN}"}
    manual = client.post("/api/events", json=EVENT, headers=good).json()
    login(client, "ivan")
    r = client.patch(f"/api/events/{manual['id']}", json={"status": "sent_to_master"})
    assert r.status_code == 200 and r.json()["status_history"][-1]["by"] == "ivan"
    client.post("/api/auth/logout")
    critical = client.post(
        "/api/events", json={**EVENT, "type": "fall", "frame_id": 2}, headers=good
    ).json()
    login(client, "petr")
    hist = next(e for e in client.get("/api/events").json() if e["id"] == critical["id"])
    assert hist["status"] == "sent_to_master"
    assert hist["status_history"][-1]["by"] == "system"
    r = client.patch(f"/api/events/{critical['id']}", json={"status": "resolved"})
    assert r.json()["status_history"][-1]["by"] == "petr"
