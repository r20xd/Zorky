import json
from datetime import datetime, timedelta, timezone

from eval.evaluate import evaluate_full
from worker.events import validate_event
from worker.offline import OFFLINE_START, load_zones, replay_frames, run_offline
from worker.rules import Detection, build_rules

FPS = 10
START = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
CONFIG = {"default": {"no_helmet": {}}}


def synth(n=40):
    for i in range(n):
        p = Detection("person", (0.4, 0.2, 0.6, 0.9), 0.9, 1, frame_aspect=1.7)
        h = Detection("head", (0.45, 0.2, 0.55, 0.3), 0.9, -1, frame_aspect=1.7)
        yield i, i / FPS, [p, h]


def test_time_is_start_plus_frame_over_fps_not_wall_clock():
    events = list(replay_frames(synth(), "c1", build_rules("c1", CONFIG), [], START))
    assert len(events) == 1
    e = events[0]
    validate_event(e)
    ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
    assert ts == START + timedelta(seconds=e["frame_id"] / FPS)
    assert e["frame_id"] > 0


def test_default_start_and_duplicates_visible():
    # два человека в разных местах: дедуп их не склеивает, дубль виден
    def two():
        for i in range(40):
            a = Detection("person", (0.1, 0.2, 0.2, 0.9), 0.9, 1, frame_aspect=1.7)
            b = Detection("person", (0.7, 0.2, 0.8, 0.9), 0.9, 2, frame_aspect=1.7)
            ha = Detection("head", (0.12, 0.2, 0.18, 0.3), 0.9, -1, frame_aspect=1.7)
            hb = Detection("head", (0.72, 0.2, 0.78, 0.3), 0.9, -1, frame_aspect=1.7)
            yield i, i / FPS, [a, b, ha, hb]

    events = list(replay_frames(two(), "c1", build_rules("c1", CONFIG), [], OFFLINE_START))
    assert len(events) == 2
    labels = [{"camera": "c1", "type": "no_helmet", "start_s": 0, "end_s": 4}]
    r = evaluate_full(events, labels, start=OFFLINE_START)["no_helmet"]
    assert r["found"] == 1 and r["dup"] == 1
    assert r["delays"][0] > 0  # задержка: правило ждёт min_frames кадров


def test_zones_file_and_silent_without_it(tmp_path):
    (tmp_path / "clip.mp4.json").write_text(json.dumps([{"id": "z"}]))
    (tmp_path / "other.json").write_text("[]")
    assert load_zones("x/clip.mp4", tmp_path) == [{"id": "z"}]
    assert load_zones("x/other.mp4", tmp_path) == []
    assert load_zones("x/none.mp4", tmp_path) == []


def test_run_offline_reads_every_frame(tmp_path):
    import cv2
    import numpy as np

    video = tmp_path / "v.mp4"
    w = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
    for _ in range(30):
        w.write(np.zeros((48, 48 + 16, 3), np.uint8))
    w.release()
    seen = []

    def people(frame):
        seen.append(1)
        return [Detection("person", (0.4, 0.2, 0.6, 0.9), 0.9, 1, frame_aspect=1.3)]

    def ppe(frame):
        return [Detection("head", (0.45, 0.2, 0.55, 0.3), 0.9, -1, frame_aspect=1.3)]

    out = tmp_path / "e.jsonl"
    n = run_offline(str(video), "v", people, ppe, out, START, tmp_path)
    assert len(seen) == 30 and n >= 1
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    event = next(e for e in lines if e["type"] == "no_helmet")
    assert event["ts"].startswith("2026-01-01T12:00:0")
    assert json.loads((tmp_path / "e.durations.json").read_text())["v"] > 2.5
