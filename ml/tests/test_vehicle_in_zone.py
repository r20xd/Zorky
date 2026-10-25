from worker.events import make_event, validate_event
from worker.pipeline import find_violations
from worker.rules import Debouncer, Detection
from worker.rules.vehicle_in_zone import VehicleInZone

ZONES = [{"id": "d1", "kind": "danger", "polygon": [[0.5, 0.5], [1, 0.5], [1, 1], [0.5, 1]]}]


def truck(x, y2=0.8, cls="truck", track=7):
    return Detection(cls, (x, y2 - 0.3, x + 0.2, y2), 0.9, track)


def run(boxes, rule=None):
    rule, debounce, found = rule or VehicleInZone(min_frames=5), Debouncer(min_frames=1), []
    for i, d in enumerate(boxes):
        found += find_violations([d] if d else [], ZONES, debounce, i * 0.1, [rule])
    return found


def test_vehicle_drives_in_fires_once_with_vehicle_box():
    xs = [0.1 + 0.05 * i for i in range(8)] + [0.55] * 15  # въехала и стоит
    found = run([truck(x) for x in xs])
    assert len(found) == 1
    v = found[0]
    assert v.type == "vehicle_in_zone" and v.zone_id == "d1"
    assert v.detection.cls == "truck" and v.detection.track_id == 7
    validate_event(
        make_event(
            camera_id="cam",
            frame_id=1,
            violation=v.type,
            bbox=v.detection.bbox,
            confidence=v.detection.confidence,
            track_id=v.detection.track_id,
            severity=v.severity,
            zone_id=v.zone_id,
        )
    )


def test_drives_past_outside_zone_silent():
    assert run([truck(0.1, y2=0.4)] * 30) == []  # ноги выше зоны
    assert run([truck(0.1 + 0.02 * i, y2=0.45) for i in range(40)]) == []


def test_short_pass_and_person_silent():
    assert run([truck(0.6)] * 4 + [truck(0.1)] * 5) == []
    assert run([Detection("person", (0.6, 0.5, 0.7, 0.9), 0.9, 1)] * 20) == []


def test_two_vehicles_two_events():
    rule, debounce, found = VehicleInZone(min_frames=2), Debouncer(min_frames=1), []
    for i in range(6):
        dets = [truck(0.55, track=1), truck(0.7, track=2)]
        found += find_violations(dets, ZONES, debounce, i * 0.1, [rule])
    assert sorted(v.detection.track_id for v in found) == [1, 2]
