import pytest

from worker.events import make_event, validate_event
from worker.pipeline import find_violations, parse_tracks
from worker.rules import Debouncer, Detection, build_rules
from worker.rules.running import Running

MACHINERY = [{"id": "machine", "kind": "machinery", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]}]


def detect(x, track=1, aspect=1.0):
    return Detection("person", (x, 0.2, x + 0.05, 0.4), 0.9, track, aspect)


def step(rule, debounce, t, x, zones=MACHINERY, track=1, aspect=1.0):
    return find_violations([detect(x, track, aspect)], zones, debounce, t, [rule])


@pytest.mark.parametrize("dt", [0.05, 0.1, 0.2])
def test_running_in_zone_fires_once_for_same_episode(dt):
    rule, debounce = Running(), Debouncer(min_frames=2)
    found = []
    for i in range(int(1.5 / dt)):
        t = i * dt
        found.extend(step(rule, debounce, t, 0.1 + 0.4 * t))
    assert len(found) == 1
    v = found[0]
    assert (v.type, v.zone_id, v.severity) == ("running", "machine", "low")
    event = make_event(
        camera_id="cam",
        frame_id=10,
        violation=v.type,
        bbox=v.detection.bbox,
        confidence=v.detection.confidence,
        track_id=v.detection.track_id,
        zone_id=v.zone_id,
        severity=v.severity,
    )
    validate_event(event)


@pytest.mark.parametrize("zones,speed", [(MACHINERY, 0.1), ([], 0.4)])
def test_walking_in_zone_or_running_outside_is_not_violation(zones, speed):
    rule, debounce = Running(), Debouncer(min_frames=1)
    for i in range(15):
        t = i * 0.1
        assert step(rule, debounce, t, 0.1 + speed * t, zones) == []


def test_smoothing_rejects_small_alternating_bbox_jitter():
    rule, debounce = Running(), Debouncer(min_frames=1)
    for i in range(50):
        assert step(rule, debounce, i * 0.05, 0.3 + 0.01 * (-1) ** i) == []


def test_frame_aspect_scales_horizontal_motion_in_heights_per_second():
    assert parse_tracks([[10, 20, 20, 40]], [0.9], [1], 200, 100)[0].frame_aspect == 2
    rule, debounce = Running(), Debouncer(min_frames=1)
    events = []
    for i in range(8):
        t = i * 0.1
        # dx=.2 высоты кадра/с на квадратном кадре = 1 рост/с, на 2:1 = 2 роста/с.
        events.extend(step(rule, debounce, t, 0.1 + 0.2 * t, aspect=2))
    assert len(events) == 1


def test_leaving_zone_or_changing_tracks_does_not_keep_old_motion():
    rule, debounce = Running(), Debouncer(min_frames=1)
    step(rule, debounce, 0, 0.1)
    step(rule, debounce, 0.1, 0.2)
    step(rule, debounce, 0.2, 0.3, [])
    assert step(rule, debounce, 0.3, 0.4) == []
    assert step(rule, debounce, 0.4, 0.6, track=2) == []


def test_duplicate_timestamp_and_clock_rewind_do_not_create_speed():
    rule, debounce = Running(), Debouncer(min_frames=1)
    for x in (0.1, 0.2, 0.3, 0.4):
        assert step(rule, debounce, 1, x) == []
    assert step(rule, debounce, 0, 0.8) == []


def test_missing_track_expires_history_and_cannot_create_jump():
    rule, debounce = Running(), Debouncer(min_frames=1)
    step(rule, debounce, 0, 0.1)
    step(rule, debounce, 0.1, 0.2)
    find_violations([], [], debounce, 3, [rule])
    assert not rule.state and not rule.last_seen
    assert step(rule, debounce, 3.1, 0.8) == []


def test_camera_configuration_changes_threshold():
    rules = build_rules("cam", {"default": {"running": {"speed_threshold": 10}}})
    debounce = Debouncer(min_frames=1)
    for i in range(10):
        assert find_violations([detect(0.1 + i * 0.04)], MACHINERY, debounce, i * 0.1, rules) == []


@pytest.mark.parametrize("params", [{"speed_threshold": 0}, {"min_samples": 1}, {"min_span_s": 2}])
def test_invalid_configuration_rejected(params):
    with pytest.raises(ValueError):
        Running(**params)
