import itertools
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from worker import main as worker
from worker.images import image_paths, read_images
from worker.pipeline import Detection


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay


def pictures(folder):
    folder.mkdir(exist_ok=True)
    for name, value in [("03.JPG", 30), ("01.jpg", 10), ("02.png", 20)]:
        assert cv2.imwrite(str(folder / name), np.full((24, 32, 3), value, dtype=np.uint8))
    (folder / "ignored.txt").write_text("not a picture")
    return folder


def test_three_images_sorted_paced_and_looped(tmp_path):
    folder = pictures(tmp_path / "frames")
    clock = Clock()
    source = read_images(str(folder), 4, loop=True, clock=clock, sleep=clock.sleep)
    values, timestamps = [], []
    for frame in itertools.islice(source, 5):
        values.append(int(frame[0, 0, 0]))
        timestamps.append(clock())
        clock.now += 0.05  # имитируем время инференса, оно входит в интервал
    source.close()
    assert values == [10, 20, 30, 10, 20]
    assert timestamps == pytest.approx([0, 0.25, 0.5, 0.75, 1.0])
    assert clock.sleeps == pytest.approx([0.2] * 4)
    assert len(list(read_images(str(folder), 10000, sleep=lambda _: None))) == 3


def test_slow_detector_does_not_burst_or_drop_images(tmp_path):
    clock = Clock()
    source = read_images(str(pictures(tmp_path)), 10, clock=clock, sleep=clock.sleep)
    times = []
    for _ in source:
        times.append(clock())
        clock.now += 0.3
    assert times == pytest.approx([0, 0.3, 0.6])
    assert clock.sleeps == []


def setup_worker(monkeypatch):
    client = Mock()
    client.get_zones.return_value = [
        {"id": "zone-1", "kind": "danger", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]}
    ]
    client.post_snapshot.return_value = "/api/snapshots/test.jpg"
    seen = []

    def detect(frame):
        seen.append(int(frame[0, 0, 0]))
        return [Detection("person", (0.4, 0.2, 0.6, 0.9), 0.9, 7)]

    monkeypatch.setattr(worker, "BackendClient", lambda _: client)
    monkeypatch.setattr(worker, "check_device", lambda _: None)
    monkeypatch.setattr(worker, "make_detectors", lambda args, cache: (detect, None))
    return client, seen


def test_folder_runs_detector_and_emits_same_event_contract(tmp_path, monkeypatch):
    folder = pictures(tmp_path / "frames")
    client, seen = setup_worker(monkeypatch)
    clock = Clock()
    monkeypatch.setattr(worker.time, "monotonic", clock)
    monkeypatch.setattr(
        worker,
        "read_images",
        lambda folder, fps, loop: read_images(folder, fps, loop, clock=clock, sleep=clock.sleep),
    )
    assert (
        worker.main(
            [
                "--images",
                str(folder),
                "--camera",
                "cam-images",
                "--fps",
                "10",
                "--loop",
                "--max-frames",
                "6",
            ]
        )
        == 0
    )
    assert seen == [10, 20, 30, 10, 20, 30]
    event = client.post_event.call_args.args[0]
    assert client.post_event.call_count == 1
    assert event["type"] == "danger_zone"
    assert event["camera_id"] == "cam-images"
    assert event["frame_id"] == 4
    assert event["track_id"] == 7
    assert event["bbox"] == [0.4, 0.2, 0.6, 0.9]
    assert event["snapshot_url"] == "/api/snapshots/test.jpg"


def test_repeated_image_sources_map_to_cameras(tmp_path, monkeypatch):
    a, b = pictures(tmp_path / "a"), pictures(tmp_path / "b")
    setup_worker(monkeypatch)
    jobs = []
    monkeypatch.setattr(worker, "run_cameras", lambda client, device, entries: jobs.extend(entries))
    assert (
        worker.main(
            [
                "--images",
                str(a),
                "--images",
                str(b),
                "--camera",
                "a",
                "--camera",
                "b",
            ]
        )
        == 0
    )
    assert [job[0] for job in jobs] == ["a", "b"]
    assert [next(job[1])[0] for job in jobs] == [0, 0]
    for _, frames, dropped in jobs:
        frames.close()
        assert dropped() == 0


@pytest.mark.parametrize("fps", ["0", "-1", "nan", "inf"])
def test_invalid_fps_rejected_before_model_load(fps):
    with pytest.raises(SystemExit):
        worker.main(["--fake", "--camera", "a", "--fps", fps])


def test_image_cli_validation(tmp_path):
    folder = pictures(tmp_path / "frames")
    with pytest.raises(SystemExit):
        worker.main(["--images", str(folder), "--camera", "a", "--camera", "b"])
    with pytest.raises(SystemExit):
        worker.main(["--images", str(folder), "--video", "a.mp4", "--camera", "a"])
    with pytest.raises(ValueError, match="не найдена"):
        image_paths(str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="нет JPG/PNG"):
        image_paths(str(tmp_path))


def test_corrupt_image_error_reaches_multicamera_cli(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "bad.jpg").write_bytes(b"not jpeg")
    good = pictures(tmp_path / "good")
    setup_worker(monkeypatch)
    result = worker.main(
        [
            "--images",
            str(bad),
            "--images",
            str(good),
            "--camera",
            "bad",
            "--camera",
            "good",
            "--loop",
            "--fps",
            "1000",
        ]
    )
    assert result == 1
    assert "bad.jpg" in capsys.readouterr().err
