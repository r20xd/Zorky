"""e2e: 10 одинаковых событий одного трека -> одно оповещение (лента и SSE), count 10.

Запуск: `py tasks.py e2e` (бэкенд уже поднят) или `python tests/e2e_dedup.py <url>`.
Только стандартная библиотека. Камеры e2e-dup-<uuid>-* уникальны на прогон:
БД бэкенда между прогонами не чистится.
"""

import json
import sys
import threading
import urllib.request
import uuid
from datetime import datetime, timezone


def event(camera: str, track: int, frame: int) -> dict:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "camera_id": camera, "ts": now, "frame_id": frame, "type": "no_helmet",
        "bbox": [0.1, 0.1, 0.3, 0.9], "confidence": 0.9, "track_id": track,
        "severity": "high", "incident_id": f"{camera}-{track}", "count": 1,
        "first_seen": now, "last_seen": now,
    }  # fmt: skip


def post(url: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{url}/api/events", json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.loads(response.read())


def listen(url: str, prefix: str, got: list, ready: threading.Event, stop: threading.Event) -> None:
    with urllib.request.urlopen(f"{url}/api/stream", timeout=30) as response:
        name = None
        for raw in response:
            line = raw.decode().rstrip("\n")
            if line == ": connected":
                ready.set()
            elif line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: ") and name == "violation":
                data = json.loads(line[6:])
                if data["camera_id"].startswith(prefix):
                    got.append(data)
            if stop.is_set():
                return


def check(url: str) -> None:
    prefix = f"e2e-dup-{uuid.uuid4().hex[:8]}-"
    cam_a, cam_b = prefix + "a", prefix + "b"
    got: list = []
    ready, stop = threading.Event(), threading.Event()
    thread = threading.Thread(target=listen, args=(url, prefix, got, ready, stop), daemon=True)
    thread.start()
    assert ready.wait(10), "SSE не подключился"

    last = None
    for frame in range(10):  # мок-воркер: один трек, 10 одинаковых событий
        last = post(url, event(cam_a, 5, frame))
    assert last["count"] == 10, f"count инцидента {last['count']}, ожидалось 10"
    post(url, event(cam_b, 5, 0))  # другая камера -> второе оповещение

    with urllib.request.urlopen(f"{url}/api/events?limit=1000", timeout=5) as response:
        feed = [e for e in json.loads(response.read()) if e["camera_id"].startswith(prefix)]
    by_cam = {e["camera_id"]: e for e in feed}
    assert len(feed) == 2, f"в ленте {len(feed)} записей, ожидалось 2"
    assert by_cam[cam_a]["count"] == 10 and by_cam[cam_b]["count"] == 1, by_cam

    thread.join(2)  # SSE шлёт сразу; короткая пауза на доставку
    stop.set()
    cams = sorted(e["camera_id"] for e in got)
    assert cams == [cam_a, cam_b], f"в SSE {cams}, ожидалось по одному на камеру"


if __name__ == "__main__":
    check(sys.argv[1])
    print("OK: e2e дедупликация — одно оповещение на нарушение")
