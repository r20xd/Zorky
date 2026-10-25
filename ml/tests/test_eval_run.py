from eval.run import (
    detection_from_dict,
    detection_to_dict,
    format_score,
    replay_rules,
    score,
    set_param,
    sweep,
)
from worker.rules import Detection

CONFIG = {"default": {"no_helmet": {}}}


def frames(n=40, fps=10, cls="head", cam_frames=None):
    out = []
    for i in range(n):
        d = Detection("person", (0.4, 0.2, 0.6, 0.9), 0.9, 1, frame_aspect=1.7)
        h = Detection(cls, (0.45, 0.2, 0.55, 0.3), 0.9, -1, frame_aspect=1.7)
        out.append({"t": i / fps, "detections": [detection_to_dict(d), detection_to_dict(h)]})
    return out


def test_roundtrip():
    d = Detection("person", (0.1, 0.2, 0.3, 0.4), 0.5, 7, frame_aspect=1.5)
    assert detection_from_dict(detection_to_dict(d)) == d


def test_one_event_per_episode_and_no_duplicates():
    events = replay_rules(frames(), "c1", CONFIG, frozenset({"no_helmet"}))
    assert [e["type"] for e in events] == ["no_helmet"]
    labels = [{"camera": "c1", "type": "no_helmet", "start_s": 0, "end_s": 4}]
    res = score(events, labels)
    assert res["no_helmet"] == {"found": 1, "missed": 0, "false": 0, "dup": 0}


def test_unlabeled_camera_events_are_false():
    events = replay_rules(frames(), "clean", CONFIG, frozenset({"no_helmet"}))
    res = score(events, [])
    assert res["no_helmet"]["false"] == 1
    assert "всего" in format_score(res)


def test_duplicates_counted():
    ev = [
        {"camera_id": "c", "type": "fall", "t": 1.0, "ts": "2000-01-01T00:00:01Z"},
        {"camera_id": "c", "type": "fall", "t": 2.0, "ts": "2000-01-01T00:00:02Z"},
    ]
    lab = [{"camera": "c", "type": "fall", "start_s": 0, "end_s": 3}]
    assert score(ev, lab)["fall"]["dup"] == 1


def test_sweep_threshold():
    cfg = {"default": {"fall": {"transition_s": 1.0}, "no_helmet": {}}}
    assert set_param(cfg, "fall", "transition_s", 2.0)["default"]["fall"] == {"transition_s": 2.0}
    assert cfg["default"]["fall"] == {"transition_s": 1.0}
    rows = sweep(
        {"c1": frames()},
        [{"camera": "c1", "type": "no_helmet", "start_s": 99, "end_s": 99}],
        cfg,
        "no_helmet",
        "min_confidence",
        [0.0, 0.95],
    )
    assert rows == [(0.0, 0, 1, 1, 0), (0.95, 0, 1, 0, 0)]


def test_pick_filters_by_substring():
    from eval.run import pick

    assert pick("gmdcsa-s1-fall-01")
    assert pick("gmdcsa-s1-fall-01", only="s1,s2")
    assert not pick("gmdcsa-s3-fall-01", only="s1,s2")
    assert not pick("gmdcsa-s3-fall-01", skip="s3")
    assert pick("mixkit-1", only="mixkit", skip="s3")
