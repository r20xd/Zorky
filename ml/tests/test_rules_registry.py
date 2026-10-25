from worker.pipeline import Detection, find_violations
from worker.rules import REGISTRY, Debouncer, Rule, Verdict, build_rules, load_config, register


def person():
    return Detection("person", (0.1, 0.1, 0.3, 0.9), 0.9, 1)


def test_default_yaml_enables_rules():
    names = [r.name for r in build_rules("cam-1", load_config())]
    assert names == [
        "danger_zone",
        "no_helmet",
        "no_respirator",
        "smoking",
        "fall",
        "immobile",
        "fence_crossing",
        "distress_signal",
    ]


def test_stub_rule_is_enabled_by_config_only():
    @register
    class Stub(Rule):
        name = "stub"

        def __init__(self, level: int = 0) -> None:
            self.level = level

        def check(self, person, detections, zones):
            return Verdict(True)

    try:
        config = {"default": {"smoking": {}}, "cameras": {"cam-9": {"stub": {"level": 2}}}}
        assert [r.name for r in build_rules("cam-1", config)] == ["smoking"]
        rules = build_rules("cam-9", config)
        assert [r.name for r in rules] == ["stub"] and rules[0].level == 2
        fired = find_violations([person()], [], Debouncer(min_frames=1), 0.0, rules)
        assert [v.type for v in fired] == ["stub"]
    finally:
        del REGISTRY["stub"]


def test_unknown_rule_in_config_is_error():
    import pytest

    with pytest.raises(ValueError):
        build_rules("c", {"default": {"nope": {}}})


def test_camera_params_change_zone_kind():
    zones = [{"id": "f", "kind": "flammable", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]}]
    rules = build_rules("c", {"default": {"danger_zone": {"zone_kind": "flammable"}}})
    fired = find_violations([person()], zones, Debouncer(min_frames=1), 0.0, rules)
    assert fired[0].zone_id == "f"


def test_disabled_rules_stay_registered():
    off = {"running", "vehicle_in_zone", "vehicle_collision", "phone", "under_load"}
    off |= {"lone_worker", "overcrowding", "at_height", "loitering"}
    enabled = {r.name for r in build_rules("cam-1", load_config())}
    assert off <= set(REGISTRY) and not off & enabled
