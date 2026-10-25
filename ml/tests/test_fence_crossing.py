from worker.pipeline import find_violations
from worker.rules import Debouncer, Detection, build_rules
from worker.rules.fence_crossing import FenceCrossing

FENCE = {
    "id": "f1",
    "kind": "fence",
    "polygon": [[0.5, 0.0], [0.6, 0.0], [0.6, 1.0], [0.5, 1.0]],
}


def person(x):
    return Detection("person", (x - 0.03, 0.4, x + 0.03, 0.8), 0.9, 1)


def run(xs, zones=(FENCE,), rule=None):
    rule, debounce, found = rule or FenceCrossing(), Debouncer(min_frames=1), []
    for i, x in enumerate(xs):
        found += find_violations([person(x)], list(zones), debounce, i * 0.1, [rule])
    return found


def test_walk_past_gives_nothing():
    assert run([0.1 + 0.01 * i for i in range(30)]) == []


def test_behind_fence_fires_once():
    found = run([0.4] * 3 + [0.55] * 10)
    assert [(v.type, v.zone_id) for v in found] == [("fence_crossing", "f1")]


def test_jump_over_thin_fence_counts_border_crossing():
    assert len(run([0.4, 0.7, 0.7, 0.7], rule=FenceCrossing(min_frames=1))) == 1


def test_other_zone_kind_ignored():
    assert run([0.55] * 10, zones=[{**FENCE, "kind": "danger"}]) == []


def test_registered():
    assert "fence_crossing" in {r.name for r in build_rules("")}
