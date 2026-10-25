from worker.events import make_event, validate_event
from worker.pipeline import find_violations
from worker.rules import Debouncer, Detection
from worker.rules.vehicle_collision import VehicleCollision


def car(x, y=0.5, track=1, cls="truck"):
    return Detection(cls, (x, y, x + 0.1, y + 0.1), 0.9, track)


def run(frames):
    rule, debounce, found = VehicleCollision(), Debouncer(min_frames=1), []
    for i, dets in enumerate(frames):
        found += find_violations(dets, [], debounce, i * 0.2, [rule])
    return found


def test_head_on_fires_once_critical():
    # зазор 0.6 -> сближаются по 0.1 за 0.2 с (0.5/с): ttc 3 с -> 1 с
    frames = [[car(0.1 + 0.05 * i, track=1), car(0.8 - 0.05 * i, track=2)] for i in range(10)]
    found = run(frames)
    assert len(found) == 1
    v = found[0]
    assert v.type == "vehicle_collision" and v.severity == "critical" and v.zone_id is None
    validate_event(
        make_event(
            camera_id="cam",
            frame_id=1,
            violation=v.type,
            bbox=v.detection.bbox,
            confidence=v.detection.confidence,
            track_id=v.detection.track_id,
            severity=v.severity,
        )
    )


def test_parallel_silent():
    frames = [
        [car(0.1 + 0.05 * i, y=0.3, track=1), car(0.1 + 0.05 * i, y=0.6, track=2)]
        for i in range(10)
    ]
    assert run(frames) == []


def test_standing_close_silent():
    assert run([[car(0.4, track=1), car(0.55, track=2)]] * 15) == []


def test_far_closing_slowly_and_moving_apart_silent():
    # ttc заведомо больше 3 с
    frames = [[car(0.05 + 0.005 * i, track=1), car(0.9 - 0.005 * i, track=2)] for i in range(10)]
    assert run(frames) == []
    frames = [[car(0.4 - 0.05 * i, track=1), car(0.5 + 0.05 * i, track=2)] for i in range(8)]
    assert run(frames) == []
