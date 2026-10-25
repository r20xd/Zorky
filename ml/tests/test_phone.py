from worker.pipeline import COCO_TRACKED, find_violations
from worker.rules import Debouncer, Detection, build_rules
from worker.rules.phone import Phone

PERSON = Detection("person", (0.4, 0.2, 0.5, 0.8), 0.9, 1)
ZONE = {
    "id": "z1",
    "kind": "machinery",
    "polygon": [[0.3, 0.5], [0.6, 0.5], [0.6, 0.9], [0.3, 0.9]],
}


def run(phone_box, rule=None, zones=(), n=8):
    rule, debounce, found = rule or Phone(), Debouncer(min_frames=1), []
    for i in range(n):
        dets = [PERSON, Detection("cell phone", phone_box, 0.8, 5)]
        found += find_violations(dets, list(zones), debounce, i * 0.1, [rule])
    return found


def test_phone_at_head_fires_once():
    found = run((0.41, 0.22, 0.45, 0.28))
    assert [v.type for v in found] == ["phone"]


def test_phone_on_table_aside_does_not_fire():
    assert run((0.7, 0.7, 0.75, 0.75)) == []


def test_needs_n_frames():
    assert run((0.41, 0.22, 0.45, 0.28), n=4) == []


def test_zone_kind_filter():
    near = (0.41, 0.22, 0.45, 0.28)
    assert len(run(near, Phone(zone_kind="machinery"), [ZONE])) == 1
    assert run(near, Phone(zone_kind="machinery")) == []


def test_registered_and_tracked():
    assert "phone" in {r.name for r in build_rules("", {"default": {"phone": {}}})}
    assert COCO_TRACKED[67] == "cell phone"
