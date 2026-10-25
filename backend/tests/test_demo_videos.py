"""Демо-видео: загрузка, список, запуск по кругу как камеры, остановка, права."""

import pytest
from fastapi.testclient import TestClient

from app import cli
from app.application.notifier import NopNotifier
from app.infrastructure.demo_player import DemoPlayer, DemoPlayerError, safe_filename
from app.infrastructure.memory_store import MemoryStore
from app.main import build_auth, create_app


class FakeProc:
    def __init__(self, cmd):
        self.cmd, self.alive = cmd, True

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.alive = False


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("MOSPROM_DEMO", "1")
    users = tmp_path / "users.json"
    cli.add_user(users, "adm", "admin", "Админ", "pw-adm")
    cli.add_user(users, "op", "operator", "Оператор", "pw-op")
    started = []
    player = DemoPlayer(
        home=tmp_path / "home",
        root=tmp_path,
        popen=lambda cmd, **kw: started.append(FakeProc(cmd)) or started[-1],
    )
    app = create_app(
        MemoryStore(),
        NopNotifier(),
        auth=build_auth(users_file=users, worker_token="t"),
        demo=player,
    )
    client = TestClient(app)

    def login(name):
        assert (
            client.post(
                "/api/auth/login", json={"login": name, "password": "pw-" + name}
            ).status_code
            == 200
        )

    return client, login, started, tmp_path


def test_safe_filename():
    assert safe_filename("C:\видео\цех 1.MP4") == "цех 1.MP4"
    assert safe_filename("../../x.avi") == "x.avi"
    for bad in ("run.exe", "", "...", "видео"):
        with pytest.raises(DemoPlayerError):
            safe_filename(bad)


def test_only_admin(setup):
    client, login, _, _ = setup
    assert client.get("/api/demo/videos").status_code == 401
    login("op")
    assert client.get("/api/demo/videos").status_code == 403


def test_upload_list_play_stop_delete(setup):
    client, login, started, root = setup
    login("adm")
    assert client.get("/api/demo/videos").json()["videos"] == []
    res = client.post("/api/demo/videos?name=цех 1.mp4", content=b"\x00\x01video-bytes")
    assert res.status_code == 201
    assert res.json()["videos"][0]["name"] == "цех 1.mp4"
    assert not list((root / "home" / "demo-videos").glob("*.part"))
    assert client.post("/api/demo/videos?name=evil.exe", content=b"x").status_code == 400
    assert client.post("/api/demo/videos?name=empty.mp4", content=b"").status_code == 400

    play = client.post("/api/demo/play", json={"camera_id": "video-1", "video": "цех 1.mp4"})
    assert play.status_code == 202
    assert play.json()["players"] == [{"camera_id": "video-1", "video": "цех 1.mp4"}]
    cmd = started[0].cmd
    assert "loop_video.py" in cmd[1] and "--camera" in cmd and "video-1" in cmd
    cameras = {c["id"]: c for c in client.get("/api/cameras").json()}
    assert cameras["video-1"]["name"] == "цех 1"  # камера заведена сама

    assert (
        client.post(
            "/api/demo/play", json={"camera_id": "bad id", "video": "цех 1.mp4"}
        ).status_code
        == 400
    )
    assert (
        client.post("/api/demo/play", json={"camera_id": "v", "video": "нет.mp4"}).status_code
        == 400
    )

    stopped = client.post("/api/demo/stop", json={"camera_id": "video-1"}).json()
    assert stopped["players"] == [] and started[0].alive is False
    assert client.delete("/api/demo/videos/цех 1.mp4").status_code == 204
    assert client.get("/api/demo/videos").json()["videos"] == []


def test_disabled_outside_demo(setup, monkeypatch):
    client, login, _, _ = setup
    monkeypatch.delenv("MOSPROM_DEMO", raising=False)
    login("adm")
    state = client.get("/api/demo/videos").json()
    assert state["enabled"] is False and state["videos"] == []
    assert (
        client.post("/api/demo/play", json={"camera_id": "v", "video": "a.mp4"}).status_code == 404
    )


def test_detect_mode_sets_camera_source_without_loop_process(setup):
    client, login, started, root = setup
    login("adm")
    client.post("/api/demo/videos?name=склад.mp4", content=b"\x00video")
    client.post("/api/demo/play", json={"camera_id": "cam-1", "video": "склад.mp4", "detect": True})
    assert started == []  # зацикливатель не запускался
    cam = next(c for c in client.get("/api/cameras").json() if c["id"] == "cam-1")
    assert cam["source"].endswith("склад.mp4")
    assert client.get("/api/demo/videos").json()["players"] == [
        {"camera_id": "cam-1", "video": "склад.mp4"}
    ]
    # повторный вызов у существующей камеры меняет источник, а не имя
    client.post("/api/demo/videos?name=другое.mp4", content=b"\x00video")
    client.post(
        "/api/demo/play", json={"camera_id": "cam-1", "video": "другое.mp4", "detect": True}
    )
    cams = {c["id"]: c for c in client.get("/api/cameras").json()}
    assert cams["cam-1"]["source"].endswith("другое.mp4") and cams["cam-1"]["name"] == "склад"


def test_detect_replay_reuses_camera_and_stop_keeps_it(setup):
    client, login, started, root = setup
    login("adm")
    client.post("/api/demo/videos?name=a.mp4", content=b"v")
    body = {"camera_id": "c1", "video": "a.mp4", "detect": True}
    client.post("/api/demo/play", json=body)
    again = client.post("/api/demo/play", json={**body, "camera_id": "c2"})
    assert again.status_code == 202
    assert [c["id"] for c in client.get("/api/cameras").json()] == ["c1"]
    assert again.json()["players"] == [{"camera_id": "c1", "video": "a.mp4"}]
    # удалить ролик, пока камера на него ссылается, нельзя
    assert client.delete("/api/demo/videos/a.mp4").status_code == 409
    stopped = client.post("/api/demo/stop", json={"camera_id": "c1"}).json()
    assert stopped["players"] == []
    cams = {c["id"]: c for c in client.get("/api/cameras").json()}
    assert cams["c1"].get("source") is None  # камера и её журнал сохранены
    assert client.delete("/api/demo/videos/a.mp4").status_code == 204


def test_loop_mode_clears_source_and_blocks_delete(setup):
    client, login, started, root = setup
    login("adm")
    client.post("/api/demo/videos?name=b.mp4", content=b"v")
    client.post("/api/demo/play", json={"camera_id": "c1", "video": "b.mp4", "detect": True})
    client.post("/api/demo/play", json={"camera_id": "c1", "video": "b.mp4", "detect": False})
    cams = {c["id"]: c for c in client.get("/api/cameras").json()}
    assert cams["c1"].get("source") is None and len(started) == 1
    assert client.delete("/api/demo/videos/b.mp4").status_code == 409
