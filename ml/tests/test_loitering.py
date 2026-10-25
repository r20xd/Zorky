from worker.pipeline import find_violations
from worker.rules import Debouncer, Detection
from worker.rules.loitering import Loitering

ZONE = {
    "id": "d1",
    "kind": "danger",
    "polygon": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.9], [0.1, 0.9]],
}


def run(x, seconds, rule=None):
    rule, deb, found = rule or Loitering(), Debouncer(min_frames=1), []
    for i in range(int(seconds * 10)):
        p = Detection("person", (x, 0.4, x + 0.05, 0.8), 0.9, 1)
        found += find_violations([p], [ZONE], deb, i * 0.1, [rule])
    return found


def test_thirty_seconds_in_zone_one_event():
    found = run(0.2, 30)
    assert [v.type for v in found] == ["loitering"] and found[0].zone_id == "d1"


def test_five_seconds_in_zone_no_event():
    assert run(0.2, 5) == []


def test_outside_zone_no_event():
    assert run(0.7, 30) == []


def test_pass_through_no_event():
    rule, deb, found = Loitering(), Debouncer(min_frames=1), []
    for i in range(60):
        x = 0.0 + i * 0.05  # зону (0.4) проходит за ~1 с
        p = Detection("person", (x, 0.4, x + 0.05, 0.8), 0.9, 1)
        found += find_violations([p], [ZONE], deb, i * 0.1, [rule])
    assert found == []
