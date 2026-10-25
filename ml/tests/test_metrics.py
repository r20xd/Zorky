import io
import json
import urllib.request

from worker import main as worker_main
from worker.client import BackendClient
from worker.metrics import MetricsReporter


def test_reporter_averages_and_resets():
    reporter = MetricsReporter("cam-1", "linq", every_s=3)
    assert reporter.report(0.0) is None
    for _ in range(30):
        reporter.frame(0.05)
    reporter.event(0.2)
    assert reporter.report(1.0) is None
    body = reporter.report(3.0)
    assert body["camera_id"] == "cam-1" and body["device"] == "linq"
    assert body["fps"] == 10.0 and body["inference_ms"] == 50.0 and body["e2e_ms"] == 200.0
    assert body["ts"].endswith("+00:00")
    assert reporter.report(6.0) is None  # окно пусто


def test_post_metrics_sends_json_to_contract_path(monkeypatch):
    seen = []

    def fake_urlopen(request, timeout):
        seen.append((request.method, request.full_url, json.loads(request.data)))
        return io.BytesIO(b"")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    BackendClient("http://b:8080").post_metrics({"camera_id": "c"})
    assert seen == [("POST", "http://b:8080/api/metrics", {"camera_id": "c"})]


def test_main_sends_metrics_periodically(monkeypatch):
    posts = []

    class FakeClient:
        def __init__(self, *_):
            pass

        def get_zones(self, camera_id):
            return []

        def get_rules(self, camera_id):
            return {}

        def post_event(self, event):
            return {}

        def post_metrics(self, metrics):
            posts.append(metrics)

    clock = iter(i * 0.1 for i in range(10000))
    monkeypatch.setattr(worker_main, "BackendClient", FakeClient)
    monkeypatch.setattr(worker_main.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(worker_main.time, "sleep", lambda _: None)
    worker_main.main(["--fake", "--camera", "cam-1", "--max-frames", "100", "--device", "linq"])
    assert len(posts) >= 2
    assert {"camera_id", "device", "fps", "inference_ms", "ts"} <= posts[0].keys()
    assert posts[0]["device"] == "linq"
