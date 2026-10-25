import pytest

from worker.events import make_event, validate_event
from worker.pipeline import find_violations
from worker.rules import Debouncer, Detection, build_rules
from worker.rules.fall import Fall
from worker.rules.immobile import Immobile

UPRIGHT = (0.3, 0.1, 0.5, 0.9)
LYING = (0.1, 0.6, 0.8, 0.8)


def frame(rules, debouncer, now, box=LYING, track=1):
    return find_violations([Detection("person", box, 0.9, track)], [], debouncer, now, rules)


@pytest.mark.parametrize("dt", [0.1, 0.5])
def test_fall_and_immobile_fire_once_during_long_lying_episode(dt):
    rules = build_rules("cam", {"default": {"fall": {}, "immobile": {}}})
    debounce = Debouncer(min_frames=2)
    assert frame(rules, debounce, 0, UPRIGHT) == []
    found = []
    for i in range(1, int(30 / dt)):
        found.extend(frame(rules, debounce, i * dt))
    assert [v.type for v in found] == ["fall", "immobile"]
    assert all(v.severity == "critical" for v in found)
    for v in found:
        event = make_event(
            camera_id="cam",
            frame_id=1,
            violation=v.type,
            bbox=v.detection.bbox,
            confidence=v.detection.confidence,
            track_id=v.detection.track_id,
            severity=v.severity,
        )
        validate_event(event)
        assert event["severity"] == "critical"


def test_standing_and_walking_do_not_raise_critical_events():
    for walking in (False, True):
        rules, debounce = [Fall(), Immobile()], Debouncer(min_frames=1)
        for i in range(150):
            x = 0.001 * i if walking else 0
            box = (0.1 + x, 0.1, 0.3 + x, 0.9)
            assert frame(rules, debounce, i * 0.2, box) == []


def test_immobile_measures_seconds_not_frames_and_requires_continuous_observations():
    rules, debounce = [Immobile()], Debouncer(min_frames=1)
    for now in range(10):
        assert frame(rules, debounce, now) == []
    assert [v.type for v in frame(rules, debounce, 10)] == ["immobile"]
    # Отсутствие наблюдений не служит доказательством неподвижности.
    assert frame(rules, debounce, 30) == []


def test_moving_lying_person_resets_stationary_anchor():
    rules, debounce = [Immobile()], Debouncer(min_frames=1)
    for i in range(100):
        # Сдвиг мал за кадр, но накопленно выходит за пределы первого anchor.
        x = (i % 20) * 0.006
        assert frame(rules, debounce, i * 0.2, (0.1 + x, 0.6, 0.8 + x, 0.8)) == []


def test_horizontal_first_observation_is_not_a_fall():
    rules, debounce = [Fall()], Debouncer(min_frames=1)
    for i in range(20):
        assert frame(rules, debounce, i * 0.1) == []


def test_slow_posture_change_and_track_replacement_are_not_falls():
    rules, debounce = [Fall(max_gap_s=5)], Debouncer(min_frames=1)
    assert frame(rules, debounce, 0, UPRIGHT) == []
    assert frame(rules, debounce, 2, LYING) == []
    assert frame(rules, debounce, 3, UPRIGHT) == []
    assert frame(rules, debounce, 3.1, LYING, track=2) == []


def test_gap_clears_fall_candidate_and_state_does_not_grow_forever():
    fall = Fall()
    debounce = Debouncer(min_frames=1)
    assert frame([fall], debounce, 0, UPRIGHT) == []
    assert frame([fall], debounce, 5, LYING) == []
    find_violations([], [], debounce, 10, [fall])
    assert not fall.state and not fall.last_seen


def test_configuration_disables_new_rules_and_changes_duration():
    assert [r.name for r in build_rules("cam", {"default": {"danger_zone": {}}})] == ["danger_zone"]
    rules = build_rules("cam", {"default": {"immobile": {"duration_s": 1}}})
    debounce = Debouncer(min_frames=1)
    assert frame(rules, debounce, 0) == []
    assert [v.type for v in frame(rules, debounce, 1)] == ["immobile"]


@pytest.mark.parametrize(
    "rule,params", [(Fall, {"transition_s": 0}), (Immobile, {"duration_s": -1})]
)
def test_invalid_parameters_rejected(rule, params):
    with pytest.raises(ValueError):
        rule(**params)
