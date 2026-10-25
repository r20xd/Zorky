import pytest

from worker.rules import Debouncer, foot_point, point_in_polygon

SQUARE = [(0.6, 0.3), (0.95, 0.3), (0.95, 0.95), (0.6, 0.95)]


def test_point_in_polygon_inside_and_outside():
    assert point_in_polygon((0.7, 0.5), SQUARE)
    assert not point_in_polygon((0.5, 0.5), SQUARE)
    assert not point_in_polygon((0.7, 0.99), SQUARE)


def test_point_in_concave_polygon():
    # Г-образная зона: вырез справа сверху зоной не считается
    shape = [(0, 0), (0.5, 0), (0.5, 0.5), (1, 0.5), (1, 1), (0, 1)]
    assert point_in_polygon((0.25, 0.25), shape)
    assert point_in_polygon((0.75, 0.75), shape)
    assert not point_in_polygon((0.75, 0.25), shape)


def test_foot_point_is_bottom_center():
    assert foot_point((0.2, 0.1, 0.4, 0.9)) == pytest.approx((0.3, 0.9))


def test_debouncer_fires_once_per_episode():
    debouncer = Debouncer(min_frames=3, window_s=2, clear_s=3)
    fired = [debouncer.update(1, "danger_zone", True, now=t * 0.5) for t in range(8)]
    assert fired == [False, False, True] + [False] * 5


def test_debouncer_repeats_while_episode_lasts():
    debouncer = Debouncer(min_frames=1, clear_s=3, repeat_s=10)
    fired = [t for t in range(0, 31) if debouncer.update(1, "x", True, t)]
    assert fired == [0, 10, 20, 30]
    assert not debouncer.update(1, "x", False, 31)
    assert debouncer.update(1, "x", True, 40)  # новый эпизод после паузы — событие сразу


def test_debouncer_without_repeat_s_never_repeats():
    debouncer = Debouncer(min_frames=1, clear_s=3)
    assert [t for t in range(0, 60) if debouncer.update(1, "x", True, t)] == [0]


def test_debouncer_blink_does_not_split_or_delay_episode():
    debouncer = Debouncer(min_frames=3, window_s=2, clear_s=3)
    seq = [True, True, False, True, False, True, True]
    fired = [debouncer.update(1, "danger_zone", a, now=t * 0.5) for t, a in enumerate(seq)]
    assert fired == [False, False, False, True, False, False, False]


def test_debouncer_slow_hits_outside_window_do_not_confirm():
    debouncer = Debouncer(min_frames=3, window_s=2, clear_s=100)
    assert not any(debouncer.update(1, "danger_zone", True, now=t * 1.5) for t in range(6))


def test_debouncer_new_episode_after_clear():
    debouncer = Debouncer(min_frames=2, window_s=2, clear_s=3)
    assert [debouncer.update(1, "x", True, t) for t in (0, 1)] == [False, True]
    assert not debouncer.update(1, "x", False, 2)
    assert [debouncer.update(1, "x", True, t) for t in (5, 6)] == [False, True]


def test_debouncer_tracks_are_independent():
    debouncer = Debouncer(min_frames=2)
    debouncer.update(1, "danger_zone", True, 0)
    assert debouncer.update(1, "danger_zone", True, 1)
    debouncer.update(2, "danger_zone", True, 1)
    assert debouncer.update(2, "danger_zone", True, 2)
