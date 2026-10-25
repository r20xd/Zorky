import pytest

from worker.overlay import VIOLATOR, box_pixels, draw_detections, label_for
from worker.rules.base import Detection


def person(track_id=3, bbox=(0.25, 0.1, 0.75, 0.9)):
    return Detection(cls="person", bbox=bbox, confidence=0.9, track_id=track_id)


def test_box_pixels_clamps_to_frame():
    assert box_pixels((0.25, 0.1, 0.75, 0.9), 200, 100) == (50, 10, 150, 90)
    assert box_pixels((-0.5, -1, 2, 3), 200, 100) == (0, 0, 200, 100)


def test_label_has_class_and_track():
    assert label_for(person(7)) == "person #7"
    assert label_for(person(-1)) == "person"


def test_draw_does_not_touch_original_and_marks_violators():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    plain = draw_detections(frame, [person()])
    flagged = draw_detections(frame, [person()], violators={3})
    assert frame.max() == 0  # исходный кадр не тронут
    assert plain.max() > 0 and not np.array_equal(plain, flagged)
    # у нарушителя на рамке есть красный (BGR VIOLATOR), у спокойного — нет
    assert (flagged == np.array(VIOLATOR, dtype=np.uint8)).all(axis=2).any()
    assert not (plain == np.array(VIOLATOR, dtype=np.uint8)).all(axis=2).any()


def test_no_detections_returns_plain_copy():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    frame = np.full((10, 10, 3), 7, dtype=np.uint8)
    assert np.array_equal(draw_detections(frame, []), frame)
