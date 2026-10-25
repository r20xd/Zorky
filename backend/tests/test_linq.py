"""Подключение к LinQ из дашборда: статус, подключить/отключить, устройство, права."""

import json

import pytest
from fastapi.testclient import TestClient

from app import cli
from app.application.notifier import NopNotifier
from app.infrastructure.linq_control import LinqControl
from app.infrastructure.memory_store import MemoryStore
from app.main import build_auth, create_app


class Spy:
    def __init__(self, ok=True):
        self.ok, self.started = ok, []

    def __call__(self, name):
        self.started.append(name)
        return self.ok


def make(tmp_path, reachable=True, ok=True):
    users = tmp_path / "users.json"
    cli.add_user(users, "adm", "admin", "Админ", "pw-adm")
    cli.add_user(users, "man", "manager", "Руководитель", "pw-man")
    cli.add_user(users, "op", "operator", "Оператор", "pw-op")
    spy = Spy(ok)
    control = LinqControl(
        home=tmp_path / "home",
        root=tmp_path,
        probe=lambda *_: reachable,
        runner=spy,
    )
    app = create_app(
        MemoryStore(),
        NopNotifier(),
        auth=build_auth(users_file=users, worker_token="t"),
        linq=control,
    )
    return TestClient(app), spy, tmp_path


def login(client, name):
    assert (
        client.post("/api/auth/login", json={"login": name, "password": "pw-" + name}).status_code
        == 200
    )


def test_status_roles(tmp_path):
    client, _, _ = make(tmp_path)
    assert client.get("/api/linq").status_code == 401
    login(client, "op")
    assert client.get("/api/linq").status_code == 403
    login(client, "man")
    assert client.get("/api/linq").status_code == 200  # руководитель только читает
    assert client.post("/api/linq/connect").status_code == 403


def test_status_connected_with_report(tmp_path):
    client, _, root = make(tmp_path)
    home = root / "home"
    home.mkdir()
    (home / "linq_report.json").write_text(
        json.dumps({"ready": True, "programs": ["a.tpu"], "broker": True, "modules": {}})
    )
    (root / "linq_up.log").write_text("запускаю\nпароль: 123\nготово\n__DONE__\n", encoding="utf-8")
    login(client, "adm")
    body = client.get("/api/linq").json()
    assert body["state"] == "connected" and body["ready"] is True
    assert body["report"]["programs"] == ["a.tpu"]
    assert body["busy"] is False
    assert body["log"] == ["запускаю", "готово"]  # строка с «пароль» не показывается


def test_disconnected_not_ready(tmp_path):
    client, _, _ = make(tmp_path, reachable=False)
    login(client, "adm")
    body = client.get("/api/linq").json()
    assert body["state"] == "disconnected" and body["ready"] is False


def test_connect_disconnect_start_tasks(tmp_path):
    client, spy, _ = make(tmp_path)
    login(client, "adm")
    assert client.post("/api/linq/connect").status_code == 202
    assert client.post("/api/linq/disconnect").status_code == 202
    assert spy.started == ["mosprom-linq-up", "mosprom-linq-down"]


def test_connect_without_task_is_409(tmp_path):
    client, _, _ = make(tmp_path, ok=False)
    login(client, "adm")
    response = client.post("/api/linq/connect")
    assert response.status_code == 409
    assert "linq-auto-setup" in response.json()["error"]


@pytest.mark.parametrize("demo,expected", [("1", "linq"), ("", "cpu")])
def test_device_default_and_choice(tmp_path, monkeypatch, demo, expected):
    if demo:
        monkeypatch.setenv("MOSPROM_DEMO", demo)
    else:
        monkeypatch.delenv("MOSPROM_DEMO", raising=False)
    client, _, root = make(tmp_path)
    login(client, "adm")
    assert client.get("/api/linq").json()["device"] == expected
    assert client.put("/api/linq/config", json={"device": "gpu"}).json()["device"] == "gpu"
    assert json.loads((root / "home" / "linq_config.json").read_text())["device"] == "gpu"
    assert client.put("/api/linq/config", json={"device": "tpu9"}).status_code == 400


def test_short_probe_failure_does_not_drop_connection(tmp_path):
    answers = iter([True, False, False, False, False])
    clock = {"t": 1000.0}
    control = LinqControl(
        home=tmp_path / "home",
        root=tmp_path,
        probe=lambda *_: next(answers),
        runner=Spy(),
        now=lambda: clock["t"],
    )
    assert control.status()["state"] == "connected"
    clock["t"] += 10  # проверка не прошла, но связь была 10 с назад
    assert control.status()["state"] == "connected"
    clock["t"] += 30  # дольше удержания: действительно обрыв
    assert control.status()["state"] == "disconnected"


def write_bench(root, loaded=True):
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "measured.json").write_text(
        json.dumps({"linq_program": "ppe16", "calibration_frames": 16}), encoding="utf-8"
    )
    results = root / "ml" / "export" / "linq_results"
    results.mkdir(parents=True)
    (results / "ppe16_accuracy.json").write_text(
        json.dumps(
            {
                "latency_ms": {"p50": 166.3, "p95": 289.3, "mean": 185.4},
                "fps_by_inflight": {"1": 5.7, "4": 12.0},
                "agreement_by_tpu_conf": {
                    "0.3": {"f1": 0.54, "recall": 0.76, "precision": 0.42},
                    "0.4": {"f1": 0.57, "recall": 0.71, "precision": 0.47},
                    "0.5": {"f1": None},
                },
            }
        ),
        encoding="utf-8",
    )
    if loaded:
        (results / "ppe16_bench_loaded.json").write_text(
            json.dumps({"latency_ms": {"p50": 949.3}, "pipelined_fps": 1.0}), encoding="utf-8"
        )


def test_status_has_bench_from_repo_results(tmp_path):
    client, _, root = make(tmp_path)
    write_bench(root)
    login(client, "man")
    bench = client.get("/api/linq").json()["bench"]
    assert bench["program"] == "ppe16" and bench["calibration_frames"] == 16
    assert bench["latency_ms"]["p50"] == 166.3
    assert bench["best"] == {"conf": 0.4, "f1": 0.57, "recall": 0.71, "precision": 0.47}
    assert bench["loaded"] == {"latency_ms": {"p50": 949.3}, "fps": 1.0}


def test_status_bench_is_absent_without_results(tmp_path):
    client, _, _ = make(tmp_path)
    login(client, "man")
    assert client.get("/api/linq").json()["bench"] is None
