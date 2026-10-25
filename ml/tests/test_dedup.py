from worker.dedup import OverlapDeduper, iou


def test_iou():
    assert iou((0, 0, 1, 1), (0, 0, 1, 1)) == 1.0
    assert iou((0, 0, 1, 1), (2, 2, 3, 3)) == 0.0


def test_second_track_on_same_person_is_dropped():
    d = OverlapDeduper()
    assert d.allow("phone", (0, 0, 0.8, 1), 3, 0.5)
    assert not d.allow("phone", (0, 0.3, 0.8, 1), 2, 1.0)


def test_other_type_other_place_other_time_and_zone_pass():
    d = OverlapDeduper(window_s=5)
    assert d.allow("phone", (0, 0, 0.4, 1), 1, 0.0)
    assert d.allow("fall", (0, 0, 0.4, 1), 2, 0.1)
    assert d.allow("phone", (0.6, 0, 1, 1), 3, 0.2)
    assert d.allow("phone", (0, 0, 0.4, 1), 4, 6.0)
    assert d.allow("danger_zone", (0, 0, 1, 1), -1, 6.0)
    assert d.allow("danger_zone", (0, 0, 1, 1), -1, 6.1)
