from datetime import time

from worker import main as worker_main
from worker.rules import build_rules
from worker.schedule import active_zones, zone_active


def zone(start=None, end=None, zid="z-1"):
    item = {"id": zid, "kind": "danger", "polygon": [[0, 0], [1, 0], [1, 1]]}
    if start:
        item["schedule"] = {"start": start, "end": end}
    return item


def test_zone_without_schedule_is_always_active():
    assert zone_active(zone(), time(3, 0))


def test_zone_active_inside_window_only():
    z = zone("07:55", "12:00")  # пример из сценария: правило до 12:00
    assert zone_active(z, time(7, 55)) and zone_active(z, time(11, 59))
    assert not zone_active(z, time(7, 54)) and not zone_active(z, time(12, 0))


def test_zone_window_across_midnight():
    z = zone("22:00", "06:00")
    assert zone_active(z, time(23, 30)) and zone_active(z, time(5, 59))
    assert not zone_active(z, time(12, 0))


def test_broken_or_empty_schedule_does_not_switch_control_off():
    assert zone_active(zone("10:00", "10:00"), time(3, 0))
    broken = zone()
    broken["schedule"] = {"start": "bad", "end": "12:00"}
    assert zone_active(broken, time(3, 0))


def test_active_zones_filters_list():
    zones = [zone(zid="always"), zone("08:00", "09:00", "morning")]
    assert [z["id"] for z in active_zones(zones, time(10, 0))] == ["always"]
    assert [z["id"] for z in active_zones(zones, time(8, 30))] == ["always", "morning"]


CONFIG = {
    "default": {"smoking": {}, "no_helmet": {}},
    "optional": {"lone_worker": {"zone_kind": "two_person", "duration_s": 30.0}},
    "cameras": {"cam-2": {"smoking": {"min_confidence": 0.5}}},
}


def names(rules):
    return [rule.name for rule in rules]


def test_enabled_list_replaces_default_set():
    assert names(build_rules("cam-1", CONFIG)) == ["smoking", "no_helmet"]
    assert names(build_rules("cam-1", CONFIG, enabled=["fall", "smoking"])) == ["fall", "smoking"]


def test_enabled_rule_takes_params_from_optional_section():
    (rule,) = build_rules("cam-1", CONFIG, enabled=["lone_worker"])
    assert rule.name == "lone_worker" and rule.duration_s == 30.0 and rule.zone_kind == "two_person"


def test_enabled_empty_means_nothing_and_unknown_is_skipped():
    assert build_rules("cam-1", CONFIG, enabled=[]) == []
    assert names(build_rules("cam-1", CONFIG, enabled=["smoking", "no_such_rule", "smoking"])) == [
        "smoking"
    ]


def test_camera_params_win_over_default():
    (rule,) = build_rules("cam-2", CONFIG, enabled=["smoking"])
    assert rule.min_confidence == 0.5


def test_fetch_enabled_rules_tolerates_backend_errors():
    class Down:
        def get_rules(self, camera_id):
            raise OSError("нет связи")

    class Fine:
        def get_rules(self, camera_id):
            return {"enabled": ["fall"], "source": "area"}

    class Default:
        def get_rules(self, camera_id):
            return {"enabled": None, "source": "default"}

    assert worker_main.fetch_enabled_rules(Down(), "c") == (False, None)
    assert worker_main.fetch_enabled_rules(Fine(), "c") == (True, ["fall"])
    assert worker_main.fetch_enabled_rules(Default(), "c") == (True, None)


class RecordingClient:
    def __init__(self, answer):
        self.answer, self.events = answer, []

    def get_zones(self, camera_id):
        return []

    def get_rules(self, camera_id):
        return self.answer

    def post_event(self, event):
        self.events.append(event)

    def post_metrics(self, metrics):
        pass

    def post_frame(self, camera_id, jpeg):
        pass

    def post_snapshot(self, jpeg):
        return ""


def run_with(answer):
    client = RecordingClient(answer)
    smoker = worker_main.fake_detections(0)
    worker_main.run_camera(client, "cam-1", "cpu", ((i, smoker, None) for i in range(30)))
    return client.events


def test_worker_follows_specialist_choice():
    everything = run_with({"enabled": None, "source": "default"})
    assert {e["type"] for e in everything}  # по умолчанию правила rules.yaml что-то находят
    assert run_with({"enabled": [], "source": "camera"}) == []  # специалист выключил всё
    only = {e["type"] for e in run_with({"enabled": ["smoking"], "source": "camera"})}
    assert only <= {"smoking"}
