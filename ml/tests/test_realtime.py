import time

from worker.realtime import LatestFrames


def test_slow_consumer_skips_frames_and_keeps_up():
    total = 40

    def source():
        yield from range(total)

    reader = LatestFrames(source(), interval_s=0.01)  # поток 100 кадров/с
    got = []
    started = time.perf_counter()
    for frame in reader:
        got.append(frame)
        time.sleep(0.05)  # медленная подмена модели: 20 кадров/с
    elapsed = time.perf_counter() - started
    assert got == sorted(got) and got[-1] >= total - 2  # идёт к свежим кадрам
    assert len(got) < total and reader.dropped > 0
    assert len(got) + reader.dropped == total
    assert elapsed < total * 0.05 * 0.7  # без пропусков ушло бы >= 2 с


def test_fast_consumer_drops_nothing():
    reader = LatestFrames(range(10), interval_s=0.02)
    assert list(reader) == list(range(10)) and reader.dropped == 0


def test_send_metrics_logs_skipped(capsys):
    from worker import main as worker_main
    from worker.metrics import MetricsReporter

    class Client:
        def post_metrics(self, body):
            pass

    reporter = MetricsReporter("c", every_s=1)
    reporter.report(0.0)
    reporter.frame(0.1)
    worker_main.send_metrics(Client(), reporter, 2.0, skipped=7)
    assert "пропущено кадров 7" in capsys.readouterr().out
