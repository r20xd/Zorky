import json

import pytest
from fastapi.testclient import TestClient

from app import cli
from app.application.notifier import NopNotifier
from app.infrastructure.memory_store import MemoryStore
from app.infrastructure.users_file import load_users
from app.main import build_auth, create_app


@pytest.fixture
def audit_log(tmp_path, monkeypatch):
    path = tmp_path / "audit.log"
    monkeypatch.setenv("MOSPROM_AUDIT", str(path))
    return path


@pytest.fixture
def setup(tmp_path, audit_log):
    path = tmp_path / "users.json"
    cli.add_user(path, "adm", "admin", "Adm", "pw-adm-long-1")
    cli.add_user(path, "ivan", "operator", "Ivan", "pw-ivan-long-1")
    auth = build_auth(users_file=path)
    app = create_app(MemoryStore(), NopNotifier(), auth=auth)
    return app, path


def client_for(app, login, password):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"login": login, "password": password})
    assert r.status_code == 200, r.text
    return c


def create(admin, login="new", role="master"):
    r = admin.post("/api/users", json={"login": login, "name": "Новый", "role": role})
    assert r.status_code == 201, r.text
    return r.json()


def test_non_admin_403(setup):
    app, _ = setup
    ivan = client_for(app, "ivan", "pw-ivan-long-1")
    assert ivan.get("/api/users").status_code == 403
    assert (
        ivan.post("/api/users", json={"login": "x", "name": "x", "role": "admin"}).status_code
        == 403
    )
    assert ivan.patch("/api/users/adm", json={"active": False}).status_code == 403
    assert ivan.post("/api/users/adm/reset-password").status_code == 403
    assert TestClient(app).get("/api/users").status_code == 401


def test_temp_password_once_and_not_stored(setup, audit_log):
    app, path = setup
    admin = client_for(app, "adm", "pw-adm-long-1")
    body = create(admin)
    temp = body["temp_password"]
    assert body["user"]["login"] == "new" and body["user"]["created_by"] == "adm"
    assert temp not in path.read_text(encoding="utf-8")
    assert temp not in audit_log.read_text(encoding="utf-8")
    listing = admin.get("/api/users").json()
    assert {u["login"] for u in listing} == {"adm", "ivan", "new"}
    assert all(
        set(u) == {"login", "name", "role", "active", "created_at", "created_by"} for u in listing
    )
    assert (
        admin.post("/api/users", json={"login": "new", "name": "x", "role": "master"}).status_code
        == 409
    )
    assert (
        admin.post("/api/users", json={"login": "a b", "name": "x", "role": "master"}).status_code
        == 400
    )


def test_admin_sets_own_password_login_and_role(setup, audit_log):
    app, path = setup
    admin = client_for(app, "adm", "pw-adm-long-1")
    body = admin.post(
        "/api/users",
        json={"login": "petr", "name": "Пётр", "role": "specialist", "password": "Petr-pass-2026"},
    ).json()
    assert body["temp_password"] is None  # пароль знает только админ
    assert body["user"]["role"] == "specialist"
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"login": "petr", "password": "Petr-pass-2026"})
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    assert "Petr-pass-2026" not in path.read_text(encoding="utf-8")
    assert "Petr-pass-2026" not in audit_log.read_text(encoding="utf-8")
    ready = admin.post(
        "/api/users",
        json={
            "login": "olga",
            "name": "Ольга",
            "role": "auditor",
            "password": "Olga-pass-2026",
            "must_change_password": False,
        },
    )
    assert ready.status_code == 201
    login = TestClient(app).post(
        "/api/auth/login", json={"login": "olga", "password": "Olga-pass-2026"}
    )
    assert "must_change_password" not in login.json() and login.json()["role"] == "auditor"
    short = admin.post(
        "/api/users", json={"login": "x1", "name": "x", "role": "master", "password": "short"}
    )
    assert short.status_code == 400 or short.status_code == 422


def test_admin_sets_password_on_reset(setup):
    app, _ = setup
    admin = client_for(app, "adm", "pw-adm-long-1")
    r = admin.post("/api/users/ivan/reset-password", json={"password": "Ivan-new-pass-1"})
    assert r.status_code == 200 and r.json()["temp_password"] is None
    after = TestClient(app).post(
        "/api/auth/login", json={"login": "ivan", "password": "Ivan-new-pass-1"}
    )
    assert after.status_code == 200 and after.json()["must_change_password"] is True
    assert admin.post("/api/users/ivan/reset-password", json={"password": "short"}).status_code in (
        400,
        422,
    )


def test_temp_login_only_password_change(setup):
    app, _ = setup
    admin = client_for(app, "adm", "pw-adm-long-1")
    temp = create(admin)["temp_password"]
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"login": "new", "password": temp})
    assert r.json()["must_change_password"] is True
    assert c.get("/api/events").status_code == 403
    assert c.get("/api/users").status_code == 403
    assert c.get("/api/auth/me").status_code == 200
    assert (
        c.post(
            "/api/auth/password", json={"old_password": "bad", "new_password": "x" * 12}
        ).status_code
        == 403
    )
    assert (
        c.post(
            "/api/auth/password", json={"old_password": temp, "new_password": "short"}
        ).status_code
        == 400
    )
    assert (
        c.post(
            "/api/auth/password", json={"old_password": temp, "new_password": temp + "0" * 10}
        ).status_code
        == 204
    )
    assert c.get("/api/events").status_code == 200
    assert "must_change_password" not in c.get("/api/auth/me").json()


def test_disable_kills_session_and_login(setup):
    app, _ = setup
    admin = client_for(app, "adm", "pw-adm-long-1")
    ivan = client_for(app, "ivan", "pw-ivan-long-1")
    assert ivan.get("/api/events").status_code == 200
    r = admin.patch("/api/users/ivan", json={"active": False})
    assert r.status_code == 200 and r.json()["active"] is False
    assert ivan.get("/api/events").status_code == 401
    r = TestClient(app).post(
        "/api/auth/login", json={"login": "ivan", "password": "pw-ivan-long-1"}
    )
    assert r.status_code == 401


def test_role_change_kills_session(setup):
    app, _ = setup
    admin = client_for(app, "adm", "pw-adm-long-1")
    ivan = client_for(app, "ivan", "pw-ivan-long-1")
    assert admin.patch("/api/users/ivan", json={"role": "auditor"}).json()["role"] == "auditor"
    assert ivan.get("/api/events").status_code == 401
    assert admin.patch("/api/users/nobody", json={"name": "x"}).status_code == 404
    assert admin.patch("/api/users/ivan", json={}).status_code == 400


def test_last_admin_protected(setup):
    app, _ = setup
    admin = client_for(app, "adm", "pw-adm-long-1")
    assert admin.patch("/api/users/adm", json={"active": False}).status_code == 409
    assert admin.patch("/api/users/adm", json={"role": "operator"}).status_code == 409
    create(admin, "adm2", "admin")
    assert admin.patch("/api/users/adm", json={"name": "Главный"}).status_code == 200
    assert admin.patch("/api/users/adm2", json={"active": False}).status_code == 200
    assert admin.patch("/api/users/adm", json={"active": False}).status_code == 409


def test_reset_password_and_audit(setup, audit_log):
    app, path = setup
    admin = client_for(app, "adm", "pw-adm-long-1")
    ivan = client_for(app, "ivan", "pw-ivan-long-1")
    temp = admin.post("/api/users/ivan/reset-password").json()["temp_password"]
    assert ivan.get("/api/events").status_code == 401
    assert admin.post("/api/users/nobody/reset-password").status_code == 404
    assert temp not in path.read_text(encoding="utf-8")
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"login": "ivan", "password": temp})
    assert r.json()["must_change_password"] is True
    admin.patch("/api/users/ivan", json={"name": "Иван"})
    rows = [json.loads(line) for line in audit_log.read_text(encoding="utf-8").splitlines()]
    assert [(r["actor"], r["action"], r["target"]) for r in rows] == [
        ("adm", "password_reset", "ivan"),
        ("adm", "user_update", "ivan"),
    ]
    assert all("ts" in r for r in rows)
    assert temp not in audit_log.read_text(encoding="utf-8")


def test_atomic_write_leaves_no_temp(setup):
    app, path = setup
    admin = client_for(app, "adm", "pw-adm-long-1")
    create(admin)
    assert sorted(p.name for p in path.parent.iterdir() if p.name.startswith("users")) == [
        "users.json"
    ]


def test_cli_add_first_admin(tmp_path, monkeypatch):
    path = tmp_path / "u.json"
    monkeypatch.setenv("MOSPROM_USERS", str(path))
    monkeypatch.setattr("getpass.getpass", lambda _: "first-admin-pw")
    assert cli.main(["add-user", "root", "admin", "--name", "Root"]) == 0
    rec = load_users(path)["root"]
    assert rec["role"] == "admin" and rec["active"] is True


def test_user_areas_create_patch_and_session(setup):
    app, path = setup
    admin = client_for(app, "adm", "pw-adm-long-1")
    r = admin.post(
        "/api/users",
        json={"login": "m1", "name": "М", "role": "master", "areas": ["A", "B"]},
    )
    assert r.status_code == 201 and r.json()["user"]["areas"] == ["A", "B"]
    assert json.loads(path.read_text(encoding="utf-8"))["m1"]["areas"] == ["A", "B"]
    r = admin.patch("/api/users/m1", json={"areas": ["C"]})
    assert r.json()["areas"] == ["C"]
    r = admin.patch("/api/users/m1", json={"areas": []})
    assert "areas" not in r.json()
    assert "areas" not in create(admin, "m2")["user"]
