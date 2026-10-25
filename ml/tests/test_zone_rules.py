from worker.events import make_event, validate_event
from worker.pipeline import find_violations
from worker.rules import Debouncer, Detection, build_rules
from worker.rules.zone_people import LoneWorker, Overcrowding

ZONE = {
    "id": "z1",
    "kind": "two_person",
    "polygon": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
}
CROWD = {**ZONE, "id": "z2", "kind": "crowd_limit"}


def person(i):
    x = 0.2 + 0.1 * i
    return Detection("person", (x, 0.4, x + 0.05, 0.8), 0.9, i + 1)


def run(rule, zone, n, seconds):
    deb, found = Debouncer(min_frames=1), []
    for i in range(int(seconds * 10)):
        found += find_violations([person(k) for k in range(n)], [zone], deb, i * 0.1, [rule])
    return found


def test_two_people_no_event():
    assert run(LoneWorker(), ZONE, 2, 40) == []


def test_one_person_40s_one_event():
    found = run(LoneWorker(), ZONE, 1, 40)
    assert [v.type for v in found] == ["lone_worker"]
    v = found[0]
    assert v.zone_id == "z1" and v.detection.track_id == -1
    assert v.detection.bbox == (0.1, 0.1, 0.9, 0.9)
    event = make_event(
        camera_id="c", frame_id=1, violation=v.type, bbox=v.detection.bbox,
        confidence=1.0, track_id=None, zone_id=v.zone_id,
    )  # fmt: skip
    validate_event(event)
    assert "track_id" not in event


def test_one_person_10s_no_event():
    assert run(LoneWorker(), ZONE, 1, 10) == []


def test_crowd_five_over_limit_one_event():
    found = run(Overcrowding(max_people=3, duration_s=10), CROWD, 5, 15)
    assert [v.type for v in found] == ["overcrowding"]


def test_crowd_at_limit_no_event():
    assert run(Overcrowding(max_people=3), CROWD, 3, 30) == []


def test_registered_in_default_config():
    config = {"default": {"lone_worker": {}, "overcrowding": {}}}
    names = [r.name for r in build_rules("cam-x", config)]
    assert "lone_worker" in names and "overcrowding" in names
