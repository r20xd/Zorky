"""Вход в один клик в демо: только при MOSPROM_DEMO=1 и только для пользователей, созданных демо."""

import json

import pytest
from fastapi.testclient import TestClient

from app import cli
from app.application.notifier import NopNotifier
from app.infrastructure.memory_store import MemoryStore
from app.main import build_auth, create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    path = tmp_path / "users.json"
    cli.add_user(path, "demo-op", "operator", "Оператор", "pw-demo-op")
    cli.add_user(path, "real", "admin", "Настоящий админ", "pw-real")
    users = json.loads(path.read_text(encoding="utf-8"))
    users["demo-op"]["created_by"] = "demo"
    path.write_text(json.dumps(users, ensure_ascii=False), encoding="utf-8")
    auth = build_auth(users_file=path, worker_token="t")
    return TestClient(create_app(MemoryStore(), NopNotifier(), auth=auth))


def test_demo_off_by_default(client, monkeypatch):
    monkeypatch.delenv("MOSPROM_DEMO", raising=False)
    assert client.get("/api/auth/demo").json() == []
    assert client.post("/api/auth/demo", json={"login": "demo-op"}).status_code == 404


def test_demo_lists_only_demo_users_without_secrets(client, monkeypatch):
    monkeypatch.setenv("MOSPROM_DEMO", "1")
    listed = client.get("/api/auth/demo").json()
    assert listed == [{"login": "demo-op", "name": "Оператор", "role": "operator"}]


def test_demo_login_gives_session(client, monkeypatch):
    monkeypatch.setenv("MOSPROM_DEMO", "1")
    response = client.post("/api/auth/demo", json={"login": "demo-op"})
    assert response.status_code == 200
    assert response.json()["role"] == "operator"
    assert client.get("/api/auth/me").json()["login"] == "demo-op"


def test_demo_login_refuses_real_users(client, monkeypatch):
    monkeypatch.setenv("MOSPROM_DEMO", "1")
    assert client.post("/api/auth/demo", json={"login": "real"}).status_code == 404
    assert client.post("/api/auth/demo", json={"login": "нет"}).status_code == 404
