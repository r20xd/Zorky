"""Замер LinQ на 1/2/4 потока: счёт кадров, пропуски и потери на подменённом стенде."""

import numpy as np
import pytest

from linq import stand_streams as ss


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeClient:
    """Стенд: отвечает через `delay` секунд после отправки; `silent` - не отвечает вовсе."""

    def __init__(self, clock, delay, silent=False):
        self.clock, self.delay, self.silent = clock, delay, silent
        self.inbox, self.count = [], 0

    def send(self, tensor):
        self.count += 1
        task_id = f"t{self.count}"
        if not self.silent:
            self.inbox.append((self.clock() + self.delay, task_id))
        return task_id

    def poll(self):
        ready = [i for t, i in self.inbox if t <= self.clock()]
        self.inbox = [(t, i) for t, i in self.inbox if t > self.clock()]
        return ready


original_run_stream = ss.run_stream


def run(client, clock, **kw):
    kw = {"fps": 10, "seconds": 10, "window": 2, **kw}
    return original_run_stream(client, np.zeros(1), clock=clock, sleep=clock.sleep, **kw)


def test_fast_stand_keeps_up_with_camera_rate():
    clock = FakeClock()
    result = run(FakeClient(clock, delay=0.05), clock)
    assert result["dropped"] == 0 and result["lost"] == 0
    assert result["done"] == result["sent"] == 100
    assert result["fps"] == pytest.approx(10, abs=0.2)
    assert 45 <= result["latency_ms"]["p50"] <= 60


def test_slow_stand_drops_frames_instead_of_queueing():
    clock = FakeClock()
    result = run(FakeClient(clock, delay=0.4), clock)  # 2 в полёте по 0.4 с = ~5 кадров/с
    assert result["dropped"] > 30
    assert result["offered"] == result["sent"] + result["dropped"]
    assert result["fps"] == pytest.approx(5, abs=0.7)
    assert result["drop_rate"] == pytest.approx(result["dropped"] / result["offered"], abs=1e-3)


def test_silent_stand_counts_lost_and_does_not_hang():
    clock = FakeClock()
    result = run(FakeClient(clock, delay=0, silent=True), clock, seconds=2)
    assert result["done"] == 0 and result["latency_ms"] is None
    assert result["lost"] == result["sent"] == 2  # окно 2: дальше только пропуски
    assert result["dropped"] > 0


def test_saturation_mode_never_drops():
    clock = FakeClock()
    result = run(FakeClient(clock, delay=0.1), clock, fps=0, seconds=5)
    assert result["dropped"] == 0
    assert result["fps"] == pytest.approx(20, abs=1.5)  # окно 2 / 0.1 с


def test_aggregate_sums_fps_and_takes_worst_latency():
    clock = FakeClock()
    a = run(FakeClient(clock, delay=0.05), clock)
    b = run(FakeClient(clock, delay=0.2), clock)
    total = ss.aggregate([a, b], 10)
    assert total["streams"] == 2
    assert total["done"] == a["done"] + b["done"]
    assert total["total_fps"] == pytest.approx((a["done"] + b["done"]) / 10, abs=0.01)
    assert total["p95_ms_worst_stream"] == b["latency_ms"]["p95"]
    assert total["fps_per_stream"] == pytest.approx(total["total_fps"] / 2, abs=0.01)


def test_run_streams_runs_every_stream_and_reports_errors():
    clock_ok = [FakeClock() for _ in range(4)]
    clients = [FakeClient(c, delay=0.05) for c in clock_ok]

    # потоки общего времени не делят: у каждого свои «часы» через клиента
    def fake_run_stream(client, tensor, fps, seconds, window):
        return run(client, clock_ok[clients.index(client)], fps=fps, seconds=seconds)

    original = ss.run_stream
    ss.run_stream = fake_run_stream
    try:
        report = ss.run_streams(clients, [np.zeros(1)] * 4, 10, 10, 2)

        def broken(client, tensor, fps, seconds, window):
            raise RuntimeError("стенд упал")

        ss.run_stream = broken
        with pytest.raises(RuntimeError, match="стенд упал"):
            ss.run_streams(clients[:2], [np.zeros(1)] * 2, 10, 10, 2)
    finally:
        ss.run_stream = original
    assert report["streams"] == 4 and len(report["per_stream"]) == 4
    assert report["total_fps"] == pytest.approx(40, abs=1)


def test_summarize_without_answers():
    result = ss.summarize([], sent=3, dropped=0, lost=3, seconds=10)
    assert result["latency_ms"] is None and result["fps"] == 0
