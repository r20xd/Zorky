import threading

import pytest

from worker.config import DEBOUNCE_MIN_FRAMES
from worker.main import main, make_detectors, run_cameras
from worker.pipeline import Detection

ZONE = {"id": "z1", "kind": "danger", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]}


class FakeClient:
    def __init__(self):
        self.events = []
        self.metrics = []
        self.lock = threading.Lock()

    def get_zones(self, camera_id):
        return [ZONE]

    def get_rules(self, camera_id):
        return {}

    def post_event(self, event):
        with self.lock:
            self.events.append(event)

    def post_metrics(self, body):
        with self.lock:
            self.metrics.append(body)

    def post_frame(self, camera_id, jpeg):
        pass

    def post_snapshot(self, jpeg):
        return "/s.jpg"


def source(track_id, n=DEBOUNCE_MIN_FRAMES + 3):
    person = Detection("person", (0.4, 0.4, 0.5, 0.9), 0.9, track_id)
    return iter([(i, [person], None) for i in range(n)])


def test_two_cameras_events_per_camera():
    client = FakeClient()
    # тот же track_id=1 на обеих камерах: состояние Debouncer у камер раздельное
    jobs = [("cam-1", source(1), lambda: 0), ("cam-2", source(1), lambda: 0)]
    run_cameras(client, "cpu", jobs)
    cams = sorted(e["camera_id"] for e in client.events)
    assert cams == ["cam-1", "cam-2"]


def test_metrics_per_camera():
    from worker import main as m

    client = FakeClient()
    ticks = iter(range(0, 10_000))
    orig = m.time.monotonic
    m.time.monotonic = lambda: next(ticks) * 2.0  # окно метрик закрывается быстро
    try:
        run_cameras(client, "cpu", [("cam-1", source(1, 30), lambda: 0)])
        run_cameras(client, "cpu", [("cam-2", source(2, 30), lambda: 0)])
    finally:
        m.time.monotonic = orig
    assert {b["camera_id"] for b in client.metrics} == {"cam-1", "cam-2"}


def test_shared_model_cache(monkeypatch):
    calls = []

    def fake_session(path, device):
        calls.append(path)
        return object()

    class Args:
        model = "m.onnx"
        ppe_weights = "p.onnx"
        device = "cpu"

    monkeypatch.setattr("worker.infer.make_session", fake_session)
    monkeypatch.setattr("worker.main.OnnxPeople", lambda s: ("people", s))
    monkeypatch.setattr("worker.main.OnnxPpe", lambda s: ("ppe", s))
    cache = {}
    a = make_detectors(Args, cache)
    b = make_detectors(Args, cache)
    assert calls == ["m.onnx", "p.onnx"]
    assert a[0][1] is b[0][1]


def test_cli_validates_cameras():
    with pytest.raises(SystemExit):
        main(["--fake", "--camera", "a", "--camera", "a"])
    with pytest.raises(SystemExit):
        main(["--video", "x.mp4", "--video", "y.mp4", "--camera", "a"])
