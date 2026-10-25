from worker.pipeline import find_violations
from worker.rules import Debouncer, Detection, build_rules
from worker.rules.under_load import UnderLoad

ZONE = {
    "id": "u1",
    "kind": "under_load",
    "polygon": [[0.4, 0.5], [0.6, 0.5], [0.6, 0.9], [0.4, 0.9]],
}


def person(x, h=0.4, w=0.12, foot=0.8):
    return Detection("person", (x - w / 2, foot - h, x + w / 2, foot), 0.9, 1)


def run(people, zones=(ZONE,)):
    rule, debounce, found = UnderLoad(), Debouncer(min_frames=1), []
    for i, p in enumerate(people):
        found += find_violations([p], list(zones), debounce, i * 0.1, [rule])
    return found


def test_walk_past_gives_nothing():
    assert run([person(0.1 + 0.02 * i, foot=0.8) for i in range(30)]) == []


def test_crouch_through_fires_once():
    walk = [person(0.2 + 0.01 * i) for i in range(20)]
    crawl = [person(0.4 + 0.01 * i, h=0.18, w=0.12) for i in range(15)]
    found = run(walk + crawl)
    assert [(v.type, v.zone_id) for v in found] == [("under_load", "u1")]


def test_crouch_beside_zone_gives_nothing():
    walk = [person(0.1) for _ in range(20)]
    assert run(walk + [person(0.2, h=0.18) for _ in range(15)]) == []


def test_slow_standing_through_edge_gives_nothing():
    xs = [0.3 + 0.004 * i for i in range(60)]
    assert run([person(x) for x in xs]) == []


def test_wide_box_without_baseline_counts():
    assert len(run([person(0.5, h=0.1, w=0.2) for _ in range(10)])) == 1


def test_registered():
    assert "under_load" in {r.name for r in build_rules("", {"default": {"under_load": {}}})}
