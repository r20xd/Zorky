"""Сквозной замер: чистые части (поток, разбор, итоги) без запуска бэкенда и воркера."""

import importlib.util
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "e2e_latency", Path(__file__).resolve().parents[1] / "demo" / "e2e_latency.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_stream_records_first_violating_frame_time():
    stream = mod.Stream(b"clean", b"viol", fps=5)
    assert stream.current() == b"clean" and stream.first_viol_at is None
    stream.start_violation()
    before = time.time()
    assert stream.current() == b"viol"
    first = stream.first_viol_at
    assert first is not None and first >= before
    stream.current()
    assert stream.first_viol_at == first  # отметка ставится один раз за заход
    stream.stop_violation()
    assert stream.current() == b"clean"
    stream.start_violation()
    assert stream.first_viol_at is None  # новый заход - новая отметка


def test_parse_ts_handles_utc_suffix():
    assert mod.parse_ts("1970-01-01T00:00:10Z") == 10.0
    assert mod.parse_ts("1970-01-01T00:00:10.500+00:00") == 10.5


def test_first_after_filters_camera_type_and_time():
    sse = mod.Sse("http://x")
    sse.items = [
        ("violation", 5.0, {"camera_id": mod.CAMERA}),  # до начала захода
        ("metrics", 12.0, {"camera_id": mod.CAMERA}),  # не то событие
        ("violation", 13.0, {"camera_id": "other"}),  # не та камера
        ("update", 14.0, {"camera_id": mod.CAMERA, "n": 2}),
        ("violation", 15.0, {"camera_id": mod.CAMERA, "n": 3}),
    ]
    name, at, body = sse.first_after(10.0, mod.CAMERA)
    assert (name, at, body["n"]) == ("update", 14.0, 2)
    assert sse.first_after(20.0, mod.CAMERA) is None


def test_summarize_skips_cold_trial_and_counts_misses():
    def hit(n, total):
        return {
            "trial": n,
            "cold": n == 1,
            "to_event_ms": total - 100,
            "to_screen_ms": 100,
            "total_ms": total,
        }

    trials = [hit(1, 9000), hit(2, 1000), hit(3, 2000), {"trial": 4, "cold": False, "missed": True}]
    summary = mod.summarize(trials)
    assert summary["trials"] == 3 and summary["detected"] == 2 and summary["missed"] == 1
    assert summary["total_ms"]["p50"] == 1500 and summary["total_ms"]["max"] == 2000
    assert summary["to_screen_ms"]["p50"] == 100


def test_summarize_without_hits_is_empty_not_fabricated():
    summary = mod.summarize([{"trial": 1, "cold": False, "missed": True}])
    assert summary["detected"] == 0 and summary["total_ms"] is None
