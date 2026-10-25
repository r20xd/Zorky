"""Своя пред-/постобработка и ByteTrack: чистые функции + сравнение с ultralytics на CPU."""

from pathlib import Path

import numpy as np
import pytest

from worker.bytetrack import ByteTracker
from worker.detect import decode, letterbox, nms

ONNX = Path(__file__).parents[1] / "yolo11n.onnx"
BUS = Path(__file__).parents[1] / ".venv/lib/python3.14/site-packages/ultralytics/assets/bus.jpg"


def test_letterbox_pads_and_scales():
    pytest.importorskip("cv2")
    frame = np.full((100, 200, 3), 50, dtype=np.uint8)
    tensor, scale, (dx, dy) = letterbox(frame, 640)
    assert tensor.shape == (1, 3, 640, 640) and tensor.dtype == np.float32
    assert scale == 3.2 and dx == 0 and dy == 160
    assert tensor[0, 0, 0, 0] == pytest.approx(114 / 255)
    assert tensor[0, 0, 320, 320] == pytest.approx(50 / 255)


def test_nms_is_per_class():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [1, 1, 11, 11]], dtype=float)
    keep = nms(boxes, np.array([0.9, 0.8, 0.7]), np.array([0, 0, 1]), 0.5)
    assert keep == [0, 2]


def test_decode_maps_back_to_frame():
    out = np.zeros((1, 6, 3))  # 2 класса, 3 кандидата
    out[0, :, 0] = [320, 320, 100, 200, 0.9, 0.1]  # центр letterbox, класс 0
    out[0, :, 1] = [320, 320, 100, 200, 0.8, 0.1]  # дубль -> NMS
    out[0, :, 2] = [10, 10, 10, 10, 0.1, 0.2]  # ниже порога
    boxes, scores, classes = decode(out, 2.0, (0.0, 100.0), (400, 640), conf=0.35)
    assert len(boxes) == 1 and classes.tolist() == [0]
    assert boxes[0].tolist() == [135.0, 60.0, 185.0, 160.0]
    assert decode(out, 2.0, (0.0, 100.0), (400, 640), 0.35, classes=[1])[0].shape == (0, 4)


def box(x):
    return [x, 100, x + 50, 250]


def run(tracker, xs, score=0.9):
    return tracker.update(np.array([box(x) for x in xs], dtype=float), np.full(len(xs), score))


def test_tracker_keeps_ids_and_assigns_new_ones():
    tracker = ByteTracker()
    assert [t[2] for t in run(tracker, [10, 200])] == [1, 2]
    assert sorted(t[2] for t in run(tracker, [14, 204])) == [1, 2]
    assert (
        run(tracker, [18, 208, 400]) and sorted(t[2] for t in run(tracker, [22, 212, 404]))[-1] == 3
    )


def test_tracker_recovers_id_after_short_loss_and_forgets_after_buffer():
    tracker = ByteTracker(track_buffer=5)
    run(tracker, [10])
    run(tracker, [14])
    for _ in range(3):
        assert run(tracker, []) == []
    assert [t[2] for t in run(tracker, [26])] == [1]
    for _ in range(7):
        run(tracker, [])
    assert not tracker.tracks


def test_weak_detection_continues_track():
    tracker = ByteTracker()
    run(tracker, [10])
    run(tracker, [14])
    out = run(tracker, [18], score=0.2)  # ниже high, выше low
    assert [t[2] for t in out] == [1]


def test_low_score_never_starts_track():
    assert run(ByteTracker(), [10], score=0.3) == []


class FakeSession:
    """Сессия, отдающая одного «человека» всегда в одном месте."""

    def get_inputs(self):
        return [type("I", (), {"name": "images"})()]

    def get_modelmeta(self):
        return type("M", (), {"custom_metadata_map": {"names": "{0: 'person', 1: 'helmet'}"}})()

    def run(self, _, feeds):
        out = np.zeros((1, 6, 1), dtype=np.float32)
        out[0, :, 0] = [320, 320, 100, 200, 0.9, 0.0]
        return [out]


def test_onnx_people_with_fake_session():
    pytest.importorskip("cv2")
    from worker.main import OnnxPeople, OnnxPpe

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    people = OnnxPeople(FakeSession())
    found = people(frame)
    assert [(d.cls, d.track_id) for d in found] == [("person", 1)]
    assert people(frame)[0].track_id == 1
    x1, y1, x2, y2 = found[0].bbox
    assert (x1, x2) == pytest.approx((270 / 640, 370 / 640))
    assert OnnxPpe(FakeSession())(frame) == []  # person не относится к СИЗ


@pytest.mark.skipif(not (ONNX.exists() and BUS.exists()), reason="нет yolo11n.onnx или bus.jpg")
def test_matches_ultralytics_on_cpu():
    pytest.importorskip("ultralytics")
    cv2 = pytest.importorskip("cv2")
    from worker.infer import make_session
    from worker.main import OnnxPeople, load_model, write_tracker_yaml, yolo_detections

    image = cv2.imread(str(BUS))
    reference = load_model(str(ONNX))
    own = OnnxPeople(make_session(str(ONNX)))
    import tempfile

    tracker = write_tracker_yaml(tempfile.mktemp(suffix=".yaml"))
    for k in range(6):
        shift = np.float32([[1, 0, 3 * k], [0, 1, 2 * k]])
        frame = cv2.warpAffine(image, shift, (image.shape[1], image.shape[0]))
        want = {d.track_id: d for d in yolo_detections(reference, frame, tracker)}
        got = {d.track_id: d for d in own(frame)}
        assert want.keys() == got.keys()
        for i, d in want.items():
            assert d.cls == got[i].cls
            assert max(abs(a - b) for a, b in zip(d.bbox, got[i].bbox, strict=True)) < 0.01


def test_decode_obb_raw_output():
    from worker.detect import decode_obb

    out = np.zeros((1, 4 + 2 + 1, 3), dtype=np.float32)
    out[0, :, 0] = [120, 120, 40, 20, 0.1, 0.9, 0.0]  # класс 1, без поворота
    out[0, :, 1] = [121, 120, 40, 20, 0.1, 0.8, 0.0]  # дубль, уйдёт по NMS
    out[0, :, 2] = [300, 300, 40, 20, 0.05, 0.05, 0.0]  # ниже порога
    hull, score, cls, rot = decode_obb(out, 2.0, (20.0, 0.0), (400, 400), 0.25)
    assert len(hull) == 1 and cls[0] == 1 and score[0] == np.float32(0.9)
    np.testing.assert_allclose(hull[0], [(100 - 20) / 2, 55, (140 - 20) / 2, 65], atol=1e-4)
    np.testing.assert_allclose(rot[0], [50, 60, 20, 10, 0.0], atol=1e-4)
