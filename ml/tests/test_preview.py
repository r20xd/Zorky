import io
import json
import urllib.request

import pytest

from worker import main as worker_main
from worker.client import BackendClient

JPEG = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9"


def test_throttle_gives_about_5_fps():
    throttle = worker_main.PreviewThrottle(5)
    sent = sum(throttle.ready(i / 30) for i in range(60))  # 2 с по 30 кадров/с
    assert 9 <= sent <= 11


def test_post_frame_sends_jpeg_to_contract_path(monkeypatch):
    seen = []

    def fake_urlopen(request, timeout):
        seen.append((request.method, request.full_url, request.data, request.headers))
        return io.BytesIO(b"")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    BackendClient("http://b:8080").post_frame("cam 1", JPEG)
    method, url, data, headers = seen[0]
    assert (method, url, data) == ("POST", "http://b:8080/api/cameras/cam%201/frame", JPEG)
    assert headers["Content-type"] == "image/jpeg"


def test_main_sends_previews_at_about_5_fps(monkeypatch):
    posts = []

    class FakeClient:
        def __init__(self, *_):
            pass

        def get_zones(self, camera_id):
            return []

        def get_rules(self, camera_id):
            return {}

        def post_metrics(self, metrics):
            pass

        def post_event(self, event):
            return json.loads(json.dumps(event))

        def post_frame(self, camera_id, jpeg):
            posts.append((camera_id, jpeg))

    clock = iter(i / 30 for i in range(1000))  # 30 кадров/с, 60 кадров = 2 с
    monkeypatch.setattr(worker_main, "BackendClient", FakeClient)
    monkeypatch.setattr(worker_main.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        worker_main,
        "fake_frames",
        lambda args, offset=0: ((i, [], lambda **_: JPEG) for i in range(60)),
    )
    assert worker_main.main(["--fake", "--camera", "cam-1"]) == 0
    assert 9 <= len(posts) <= 11
    assert all(p == ("cam-1", JPEG) for p in posts)


def test_post_snapshot_returns_url(monkeypatch):
    seen = []

    def fake_urlopen(request, timeout):
        seen.append((request.method, request.full_url, request.data))
        return io.BytesIO(b'{"url": "/api/snapshots/evt-000001.jpg"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    url = BackendClient("http://b:8080").post_snapshot(JPEG)
    assert url == "/api/snapshots/evt-000001.jpg"
    assert seen == [("POST", "http://b:8080/api/snapshots", JPEG)]


def test_encode_jpeg_draws_box():
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    plain = worker_main.encode_jpeg(frame, width=200)
    boxed = worker_main.encode_jpeg(frame, width=200, box=(0.1, 0.1, 0.9, 0.9))
    decoded = cv2.imdecode(np.frombuffer(boxed, np.uint8), cv2.IMREAD_COLOR)
    assert plain != boxed
    assert decoded[:, :, 2].max() > 100  # красная рамка на чёрном кадре


def test_event_gets_snapshot_url(monkeypatch):
    events = []
    boxes = []

    class FakeClient:
        def __init__(self, *_):
            pass

        def get_zones(self, camera_id):
            return []

        def get_rules(self, camera_id):
            return {}

        def post_metrics(self, metrics):
            pass

        def post_event(self, event):
            events.append(event)

        def post_frame(self, camera_id, jpeg):
            pass

        def post_snapshot(self, jpeg):
            assert jpeg == JPEG
            return "/api/snapshots/evt-000001.jpg"

    flagged = []

    def make_jpeg(box=None, overlay=False, violators=None):
        boxes.append(box)
        if overlay:
            flagged.append(violators)
        return JPEG

    smoker = worker_main.fake_detections(0)
    monkeypatch.setattr(worker_main, "BackendClient", FakeClient)
    monkeypatch.setattr(
        worker_main,
        "fake_frames",
        lambda args, offset=0: ((i, smoker, make_jpeg) for i in range(60)),
    )
    clock = iter(i / 10 for i in range(1000))
    monkeypatch.setattr(worker_main.time, "monotonic", lambda: next(clock))
    assert worker_main.main(["--fake", "--camera", "cam-1"]) == 0
    assert events
    assert all(e["snapshot_url"] == "/api/snapshots/evt-000001.jpg" for e in events)
    assert len([b for b in boxes if b is not None]) == len(events)
    # после события его нарушитель красный на следующих превью
    tracks = {e["track_id"] for e in events if "track_id" in e}
    assert tracks and any(v and v <= tracks for v in flagged)
