import logging

from worker.events import make_event, validate_event
from worker.pipeline import COCO_TRACKED, find_violations, parse_tracks
from worker.rules import Debouncer, Detection, build_rules
from worker.rules.vehicle_proximity import VehicleProximity, bbox_gap

PERSON = (0.1, 0.4, 0.15, 0.6)  # высота 0.2


def frame(vehicle_x, cls="truck", track=7):
    return [
        Detection("person", PERSON, 0.9, 1),
        Detection(cls, (vehicle_x, 0.3, vehicle_x + 0.2, 0.7), 0.9, track),
    ]


def run(xs, rule=None, cls="truck", dt=0.1):
    rule, debounce = rule or VehicleProximity(), Debouncer(min_frames=1)
    found = []
    for i, x in enumerate(xs):
        found += find_violations(frame(x, cls), [], debounce, i * dt, [rule])
    return found


def test_gap_normalized_and_zero_on_overlap():
    assert abs(bbox_gap((0, 0, 0.1, 0.1), (0.3, 0, 0.4, 0.1)) - 0.2) < 1e-9
    assert bbox_gap((0, 0, 0.2, 0.2), (0.1, 0.1, 0.3, 0.3)) == 0


def test_static_close_vehicle_fires_once_after_n_frames(caplog):
    caplog.set_level(logging.INFO, logger="worker.vehicle_proximity")
    found = run([0.18] * 12)  # зазор 0.03 = 0.15 роста < 0.3
    assert len(found) == 1
    v = found[0]
    assert (v.type, v.severity) == ("vehicle_proximity", "critical")
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
    assert "truck" in caplog.text and "зазор" in caplog.text


def test_fewer_than_n_frames_or_interrupted_streak_silent():
    assert run([0.18] * 4) == []
    assert run([0.18, 0.18, 0.18, 0.18, 0.9, 0.18, 0.18, 0.18, 0.18, 0.9]) == []


def test_far_vehicle_silent():
    assert run([0.5] * 15) == []  # зазор 1.75 роста


def test_moving_vehicle_has_stricter_threshold():
    # Зазор 0.5 роста: для стоящей техники безопасно, для движущейся — нарушение.
    assert run([0.25] * 15) == []
    # Техника подъезжает со скоростью 0.3/с (1.5 её ширины в секунду): движется.
    approaching = [0.6 - 0.03 * i for i in range(15)]
    assert run(approaching) != []
    assert run([0.4 + 0.03 * i for i in range(15)]) == []


def test_non_vehicle_class_ignored():
    assert run([0.18] * 12, cls="helmet") == []


def test_registry_and_parse_tracks_vehicle_classes():
    rules = build_rules("", {"default": {"vehicle_proximity": {}}})
    assert "vehicle_proximity" in {r.name for r in rules}
    found = parse_tracks([[0, 0, 10, 10], [0, 0, 20, 20]], [0.9, 0.8], [1, 2], 100, 100, [0, 7])
    assert [d.cls for d in found] == ["person", "truck"]
    assert set(COCO_TRACKED.values()) >= {"car", "truck", "bus", "train"}
