from worker.pipeline import find_violations
from worker.rules import Debouncer, Detection, build_rules
from worker.rules.distress_signal import DistressSignal


def pose(left_up=True, right_up=True):
    kp = [(0.45, 0.5, 0.9)] * 17
    kp[0], kp[1], kp[2] = (0.45, 0.30, 0.9), (0.44, 0.28, 0.9), (0.46, 0.28, 0.9)
    kp[9] = (0.40, 0.1 if left_up else 0.6, 0.9)
    kp[10] = (0.50, 0.1 if right_up else 0.6, 0.9)
    return tuple(kp)


def run(seconds, **kw):
    rule, debounce, found = DistressSignal(), Debouncer(min_frames=1), []
    for i in range(int(seconds * 10)):
        person = Detection("person", (0.4, 0.2, 0.5, 0.9), 0.9, 1, keypoints=pose(**kw))
        found += find_violations([person], [], debounce, i * 0.1, [rule])
    return found


def test_two_hands_3s_no_event():
    assert run(3) == []


def test_two_hands_6s_fires_once_critical():
    found = run(6)
    assert [v.type for v in found] == ["distress_signal"]
    assert found[0].severity == "critical"


def test_one_hand_no_event():
    assert run(5, right_up=False) == []


def test_two_hands_1s_no_event():
    assert run(1) == []


def test_yaml_default_hold_5s():
    rule = next(r for r in build_rules("cam-x", {"default": {"distress_signal": {"hold_s": 5}}}))
    assert rule.hold_s == 5


def test_registered_in_default_config():
    # правило выключено в rules.yaml (контрольная точка), но регистрируется и собирается из конфига
    rules = build_rules("cam-x", {"default": {"distress_signal": {}}})
    assert [r.name for r in rules] == ["distress_signal"]
