import numpy as np
import pytest

from eval import small_objects as so


def test_person_crop_margin_and_clamp():
    assert so.person_crop([100, 100, 200, 300], 640, 480, 0.1) == (90, 80, 210, 320)
    assert so.person_crop([-5, 0, 50, 480], 640, 480, 0.5) == (0, 0, 78, 480)
    x1, y1, x2, y2 = so.person_crop([639.9, 10, 640, 20], 640, 480, 0.0)
    assert x2 > x1 and y2 > y1 and x2 <= 640


def test_boxes_to_frame_shift():
    out = so.boxes_to_frame(np.array([[1, 2, 3, 4]]), (10, 20, 50, 60))
    assert out.tolist() == [[11, 22, 13, 24]]


def test_shrink_scene_helmet_size_and_labels():
    img = np.full((480, 640, 3), 200, np.uint8)
    lab = np.array([[1, 0.5, 0.5, 0.1, 0.1], [2, 0.2, 0.2, 0.05, 0.05]], np.float32)
    canvas, out, s = so.shrink_scene(img, lab, 16)
    assert canvas.shape == (640, 640, 3)
    hel = out[0]
    assert abs(np.sqrt(hel[3] * hel[4]) * 640 - 16) < 0.5
    assert abs(hel[1] - 0.5) < 0.01 and s < 1
    assert so.shrink_scene(img, lab[1:], 16) is None


def test_nms_and_map_perfect():
    b = np.array([[0, 0, 10, 10], [1, 1, 10, 10], [0, 0, 10, 10]], np.float32)
    keep = so.nms_classwise(b, np.array([0.9, 0.8, 0.7]), np.array([1, 1, 2]))
    assert sorted(keep) == [0, 2]
    pytest.importorskip("ultralytics")
    gt = np.array([[0, 0, 10, 10]], np.float32)
    st = [
        {
            "pred": gt,
            "conf": np.array([0.9]),
            "pcls": np.array([1]),
            "gt": gt,
            "gcls": np.array([1]),
        }
    ]
    m = so.compute_map(st)
    assert m["classes"]["helmet"]["mAP50"] > 0.99 and m["mAP50-95"] > 0.99
