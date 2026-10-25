import pytest

from export.quantize import default_out, list_images, quantize


def test_default_out():
    assert default_out("a/best.onnx") == str(__import__("pathlib").Path("a/best.int8.onnx"))


def test_list_images(tmp_path):
    for n in ("b.jpg", "a.PNG", "c.txt"):
        (tmp_path / n).write_bytes(b"x")
    assert [p.name for p in list_images(str(tmp_path))] == ["a.PNG", "b.jpg"]
    assert len(list_images(str(tmp_path), limit=1)) == 1


def test_quantize_requires_images(tmp_path):
    with pytest.raises(ValueError):
        quantize("x.onnx", [])


def test_quantize_tiny_model(tmp_path):
    onnx = pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from onnx import TensorProto, helper, numpy_helper

    w = numpy_helper.from_array(np.random.rand(4, 3, 3, 3).astype(np.float32), "w")
    node = helper.make_node("Conv", ["images", "w"], ["out"], pads=[1, 1, 1, 1])
    graph = helper.make_graph(
        [node],
        "tiny",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 32, 32])],
        [helper.make_tensor_value_info("out", TensorProto.FLOAT, [1, 4, 32, 32])],
        [w],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    src = tmp_path / "tiny.onnx"
    onnx.save(model, str(src))
    imgs = [tmp_path / f"{i}.jpg" for i in range(3)]
    out = quantize(
        str(src),
        imgs,
        imgsz=32,
        load_image=lambda p: np.random.randint(0, 255, (48, 64, 3), dtype=np.uint8),
    )
    q = onnx.load(out)
    assert any(n.op_type in ("QuantizeLinear", "QLinearConv") for n in q.graph.node)
