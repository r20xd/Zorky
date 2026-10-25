import pytest

from export.linq import check_onnx, collect_calibration, export_model


def _tiny(path, shape, with_nms=False):
    onnx = pytest.importorskip("onnx")
    np = pytest.importorskip("numpy")
    from onnx import TensorProto, helper, numpy_helper

    w = numpy_helper.from_array(np.random.rand(4, 3, 3, 3).astype(np.float32), "w")
    nodes = [helper.make_node("Conv", ["images", "w"], ["out"], pads=[1, 1, 1, 1])]
    if with_nms:
        nodes.append(helper.make_node("NonMaxSuppression", ["a", "b"], ["c"]))
    graph = helper.make_graph(
        nodes,
        "tiny",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, shape)],
        [helper.make_tensor_value_info("out", TensorProto.FLOAT, [1, 4, 32, 32])],
        [w],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 12)])
    model.ir_version = 8
    onnx.save(model, str(path))
    return str(path)


def test_check_ok(tmp_path):
    assert check_onnx(_tiny(tmp_path / "a.onnx", [1, 3, 32, 32]), 32) == []


def test_check_dynamic_input(tmp_path):
    problems = check_onnx(_tiny(tmp_path / "a.onnx", ["b", 3, 32, 32]), 32)
    assert any("статический" in p for p in problems)


def test_check_wrong_size_and_nms(tmp_path):
    problems = check_onnx(_tiny(tmp_path / "a.onnx", [1, 3, 32, 32], with_nms=True), 64)
    assert len(problems) == 2


def test_export_model_rejects_bad_onnx(tmp_path, monkeypatch):
    bad = _tiny(tmp_path / "bad.onnx", ["b", 3, 32, 32])

    class Fake:
        def export(self, **kwargs):
            assert kwargs["nms"] is False and kwargs["dynamic"] is False and kwargs["batch"] == 1
            return bad

    import worker.infer

    monkeypatch.setattr(worker.infer, "load_model", lambda w: Fake())
    with pytest.raises(RuntimeError):
        export_model("x.pt", str(tmp_path / "out" / "m.onnx"), 32)


def test_collect_calibration(tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    src = tmp_path / "src"
    src.mkdir()
    for i in range(5):
        (src / f"{i}.jpg").write_bytes(b"x")
    saved = collect_calibration(
        str(src),
        str(tmp_path / "calib"),
        count=3,
        imgsz=16,
        load_image=lambda p: np.zeros((20, 30, 3), dtype=np.uint8),
    )
    assert len(saved) == 3 and all(p.exists() for p in saved)


def test_calibration_from_videos(tmp_path):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    clip = tmp_path / "videos" / "a.mp4"
    clip.parent.mkdir()
    writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
    for i in range(30):
        writer.write(np.full((48, 64, 3), i * 8, np.uint8))
    writer.release()
    saved = collect_calibration(str(clip.parent), str(tmp_path / "calib"), count=5, imgsz=32)
    assert 1 <= len(saved) <= 5
    assert cv2.imread(str(saved[0])).shape[:2] == (32, 32)
