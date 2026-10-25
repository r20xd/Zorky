"""Живая лента по HTTP: настоящий uvicorn в потоке (TestClient бесконечный поток не отдаёт)."""

import socket
import threading
import time

import httpx
import uvicorn

from app.application.notifier import NopNotifier
from app.infrastructure.memory_store import MemoryStore
from app.main import create_app

EVENT = {
    "camera_id": "cam-1",
    "ts": "2026-10-21T10:00:00Z",
    "frame_id": 1,
    "type": "smoking",
    "bbox": [0.1, 0.1, 0.2, 0.3],
    "confidence": 0.7,
    "track_id": 1,
}


def test_stream_endpoint_sends_violation_over_http():
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
        with httpx.stream("GET", f"{base}/api/stream", timeout=5) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = response.iter_lines()
            assert next(lines) == ": connected"
            assert httpx.post(f"{base}/api/events", json=EVENT).status_code == 201
            seen = [next(lines) for _ in range(3)]
            # повтор в окне склейки приходит как update с растущим счётчиком
            again = httpx.post(f"{base}/api/events", json={**EVENT, "frame_id": 2})
            assert again.status_code == 201
            repeat = [next(lines) for _ in range(3)]
        assert "event: violation" in seen
        assert any(line.startswith("data: ") and '"smoking"' in line for line in seen)
        assert "event: update" in repeat
        assert any('"count":2' in line for line in repeat)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
