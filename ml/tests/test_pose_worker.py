from types import SimpleNamespace

from worker.main import PosePeople, attach_poses, pose_detections
from worker.rules import Detection


class Arr(list):
    def tolist(self):
        return list(self)


def fake_model(box, xy):
    result = SimpleNamespace(
        boxes=SimpleNamespace(xyxy=Arr([box]), conf=Arr([0.9])),
        keypoints=SimpleNamespace(xy=Arr([xy]), conf=Arr([[0.9] * 17])),
    )
    return SimpleNamespace(predict=lambda *a, **k: [result])


FRAME = SimpleNamespace(shape=(100, 200, 3))


def test_pose_attached_to_matching_track_normalized():
    xy = [(100.0, 50.0)] * 17
    model = fake_model([40, 10, 120, 90], xy)
    person = Detection("person", (0.2, 0.1, 0.6, 0.9), 0.9, 7)
    far = Detection("person", (0.8, 0.1, 0.9, 0.2), 0.9, 8)
    found = PosePeople(lambda f: [person, far], model)(FRAME)
    assert found[0].keypoints[0] == (0.5, 0.5, 0.9)
    assert found[1].keypoints is None


def test_callable_ultralytics_like_model_uses_predict():
    model = fake_model([40, 10, 120, 90], [(100.0, 50.0)] * 17)

    class Callable:
        predict = model.predict

        def __call__(self, *a, **k):
            return ["raw Results"]

    person = Detection("person", (0.2, 0.1, 0.6, 0.9), 0.9, 7)
    found = PosePeople(lambda f: [person], Callable())(FRAME)
    assert found[0].keypoints[0] == (0.5, 0.5, 0.9)


def test_no_keypoints_result_is_empty():
    model = SimpleNamespace(predict=lambda *a, **k: [SimpleNamespace(boxes=None, keypoints=None)])
    assert pose_detections(model, FRAME) == []
    person = Detection("person", (0, 0, 1, 1), 0.9, 1)
    assert attach_poses([person], [], 200, 100) == [person]


def test_pose_model_loaded_only_with_pose_rule(monkeypatch):
    import argparse

    from worker import main as m
    from worker.rules.base import needs_pose

    cfg_on = {"default": {"distress_signal": {}, "fall": {}}}
    cfg_off = {"default": {"fall": {}}}
    assert needs_pose("c", cfg_on) and not needs_pose("c", cfg_off)

    loaded = []
    monkeypatch.setattr(m, "load_model", lambda path, device: loaded.append(path) or object())
    args = argparse.Namespace(
        model="yolo11n.pt", ppe_weights=None, device="cpu", pose_model="yolo11n-pose.pt"
    )
    monkeypatch.setattr(m, "needs_pose", lambda camera_id: camera_id == "with")
    m.make_detectors(m.args_for_camera(args, "without"), {})
    assert loaded == ["yolo11n.pt"]
    people, _ = m.make_detectors(m.args_for_camera(args, "with"), {})
    assert loaded[1:] == ["yolo11n.pt", "yolo11n-pose.pt"]
    assert isinstance(people, PosePeople)


def test_onnx_pose_and_pose_time_separately(monkeypatch):
    import numpy as np

    from worker.detect import decode_pose
    from worker.main import OnnxPose
    from worker.metrics import MetricsReporter, pose_clock

    out = np.zeros((1, 56, 1), dtype=np.float32)
    out[0, :5, 0] = [100, 100, 40, 80, 0.9]  # letterbox 1:1
    out[0, 5:, 0] = np.tile([100, 90, 0.8], 17)
    ((box, kp),) = decode_pose(out, 1.0, (0.0, 0.0), (200, 200), 0.3)
    assert box == (80.0, 60.0, 120.0, 140.0) and kp[0] == (100.0, 90.0, np.float32(0.8))

    session = SimpleNamespace(
        get_inputs=lambda: [SimpleNamespace(name="images")], run=lambda *a: [out]
    )
    frame = SimpleNamespace(shape=(200, 200, 3))
    monkeypatch.setattr("worker.detect.letterbox", lambda f, size: (None, 1.0, (0.0, 0.0)))
    poses = OnnxPose(session)(frame)
    assert poses[0][0] == (0.4, 0.3, 0.6, 0.7)

    pose_clock.take()
    person = Detection("person", (0.4, 0.3, 0.6, 0.7), 0.9, 1)
    found = PosePeople(lambda f: [person], OnnxPose(session))(frame)
    assert found[0].keypoints[0][:2] == (0.5, 0.45)
    reporter = MetricsReporter("c", every_s=1)
    reporter.frame(0.1, pose_clock.take())
    reporter.report(0.0)
    reporter.report(2.0)
    assert reporter.last_pose_ms is not None
