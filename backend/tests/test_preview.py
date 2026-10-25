"""Превью: JPEG воркера -> MJPEG камеры (настоящий uvicorn: TestClient поток не отдаёт)."""

import socket
import threading
import time

import httpx
import uvicorn
from fastapi.testclient import TestClient

from app.application.notifier import NopNotifier
from app.infrastructure.memory_store import MemoryStore
from app.main import create_app

JPEG = b"\xff\xd8\xff\xe0fake-jpeg-body\xff\xd9"


def test_frame_rejects_non_jpeg():
    client = TestClient(create_app(MemoryStore(), NopNotifier()))
    response = client.post("/api/cameras/cam-1/frame", content=b"nope")
    assert response.status_code == 400
    assert "error" in response.json()
    headers = {"content-type": "image/jpeg"}
    ok = client.post("/api/cameras/cam-1/frame", content=JPEG, headers=headers)
    assert ok.status_code == 204


def test_mjpeg_serves_posted_frame():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(MemoryStore(), NopNotifier()), port=port, log_level="error", lifespan="off"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                httpx.get(f"{base}/healthz")
                break
            except httpx.HTTPError:
                time.sleep(0.05)
        url = f"{base}/api/cameras/cam-1/mjpeg"
        with httpx.stream("GET", url, timeout=5) as response:
            assert response.status_code == 200
            content_type = response.headers["content-type"]
            assert content_type.startswith("multipart/x-mixed-replace")
            assert "boundary=frame" in content_type
            post = httpx.post(f"{base}/api/cameras/cam-1/frame", content=JPEG)
            assert post.status_code == 204
            data = b""
            for chunk in response.iter_bytes():
                data += chunk
                if JPEG in data:
                    break
        assert data.startswith(b"--frame\r\nContent-Type: image/jpeg\r\n")
        assert f"Content-Length: {len(JPEG)}".encode() in data
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_phone_camera_frames_register_camera_and_stream(monkeypatch):
    """Кадры со страницы телефона: камера заводится сама, поток отдаётся с токеном воркера."""
    from fastapi.testclient import TestClient

    from app.application.notifier import NopNotifier
    from app.infrastructure.memory_store import MemoryStore
    from app.main import create_app

    monkeypatch.setenv("PORT", "9191")
    client = TestClient(create_app(MemoryStore(), NopNotifier()))  # режим разработки: всё открыто
    assert client.post("/api/phone/phone-1/frame", content=b"not jpeg").status_code == 400
    jpeg = b"\xff\xd8\xff\xe0" + b"x" * 20
    assert client.post("/api/phone/phone-1/frame", content=jpeg).status_code == 204
    cameras = client.get("/api/cameras").json()
    phone = next(c for c in cameras if c["id"] == "phone-1")
    assert phone["name"] == "Камера телефона"
    assert phone["source"].startswith("http://127.0.0.1:9191/api/phone/phone-1.mjpg")


def test_phone_frame_reaches_operator_preview_unless_worker_is_active():
    from app.infrastructure.previews import Previews

    previews = Previews()
    previews.put("phone-1", b"raw")
    assert previews.latest("phone-1")[1] == b"raw"
    previews.put_from_worker("phone-1", b"boxes")
    assert previews.worker_active("phone-1") and previews.latest("phone-1")[1] == b"boxes"
    assert not previews.worker_active("другая")


def test_phone_frame_takes_camera_name_from_query():
    from fastapi.testclient import TestClient

    from app.application.notifier import NopNotifier
    from app.infrastructure.memory_store import MemoryStore
    from app.main import create_app

    client = TestClient(create_app(MemoryStore(), NopNotifier()))
    jpeg = b"\xff\xd8\xff\xe0" + b"x" * 20
    url = "/api/phone/pc-1/frame"
    assert client.post(url + "?name=Вебкамера компьютера", content=jpeg).status_code == 204
    assert client.post("/api/phone/pc-2/frame?name=" + "я" * 100, content=jpeg).status_code == 204
    assert client.post("/api/phone/pc-3/frame", content=jpeg).status_code == 204
    names = {c["id"]: c["name"] for c in client.get("/api/cameras").json()}
    assert names["pc-1"] == "Вебкамера компьютера"
    assert names["pc-2"] == "я" * 64
    assert names["pc-3"] == "Камера телефона"


def test_specialist_can_send_phone_frames(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import cli
    from app.application.notifier import NopNotifier
    from app.infrastructure.memory_store import MemoryStore
    from app.main import build_auth, create_app

    monkeypatch.setenv("MOSPROM_AUDIT", str(tmp_path / "audit.log"))
    users = tmp_path / "users.json"
    cli.add_user(users, "spec", "specialist", "spec", "pw-spec-long-1")
    cli.add_user(users, "adm", "admin", "adm", "pw-adm-long-1")
    app = create_app(MemoryStore(), NopNotifier(), auth=build_auth(users_file=users))
    jpeg = b"\xff\xd8\xff\xe0" + b"x" * 20
    for login in ("spec", "adm"):
        c = TestClient(app)
        r = c.post("/api/auth/login", json={"login": login, "password": f"pw-{login}-long-1"})
        assert r.status_code == 200
        assert c.post("/api/phone/pc-1/frame", content=jpeg).status_code == 204
    assert TestClient(app).post("/api/phone/pc-1/frame", content=jpeg).status_code == 401
