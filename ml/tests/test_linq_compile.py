import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from export.linq_compile import Sdk, build_program, calibration_images, load_frames
from tests.test_linq_export import _tiny


class FakeSdk:
    def __init__(self, fail_o5=True):
        self.calls = []
        self.fail_o5 = fail_o5
        self.version = "9.9.9"

    def condition(self, model):
        self.calls.append(f"condition:{type(model).__name__}")
        return model

    def calibrate(self, model, input_name, frames):
        self.calls.append(f"calibrate:{input_name}:{frames.shape[0]}")
        return "cal"

    def quantize(self, calibrated):
        self.calls.append("quantize")
        return "quant"

    def compile(self, quant, preset, out):
        self.calls.append(f"compile:{preset}")
        if preset == "O5" and self.fail_o5:
            raise RuntimeError("no")
        open(out, "wb").write(b"tpu")


def test_build_program_falls_back_to_o1(tmp_path):
    np = pytest.importorskip("numpy")
    onnx = _tiny(tmp_path / "m.onnx", [1, 3, 640, 640])
    sdk = FakeSdk()
    frames = np.zeros((3, 3, 4, 4), np.float32)
    desc = build_program(onnx, str(tmp_path / "o"), "m", sdk, [], {0: "person"}, frames=frames)
    assert sdk.calls == [
        "condition:ModelProto",
        "calibrate:images:3",
        "quantize",
        "compile:O5",
        "compile:O1",
    ]
    tpu = tmp_path / "o" / "m_b1_o1_h1m.tpu"
    saved = json.loads(open(str(tpu) + ".json", encoding="utf-8").read())
    assert saved == desc and desc["file"] == tpu.name
    assert desc["input"] == "images" and desc["outputs"] == ["out"]
    assert desc["preset"] == "O1" and desc["sdk_version"] == "9.9.9" and desc["batch"] == 1
    assert len(desc["sha256"]) == 64 and desc["names"] == {"0": "person"}


def test_build_program_o5_ok(tmp_path):
    np = pytest.importorskip("numpy")
    onnx = _tiny(tmp_path / "m.onnx", [1, 3, 640, 640])
    desc = build_program(
        onnx,
        str(tmp_path),
        "m",
        FakeSdk(fail_o5=False),
        [],
        frames=np.zeros((1, 3, 4, 4), np.float32),
    )
    assert desc["file"] == "m_b1_o5_h1m.tpu" and (tmp_path / desc["file"]).is_file()


def test_rejects_bad_onnx(tmp_path):
    onnx = _tiny(tmp_path / "m.onnx", [1, 3, 32, 32])
    with pytest.raises(ValueError):
        build_program(onnx, str(tmp_path), "m", FakeSdk(), [])


def test_calibration_needs_real_frames(tmp_path):
    with pytest.raises(FileNotFoundError):
        calibration_images([str(tmp_path)])
    for i in range(210):
        (tmp_path / f"{i}.jpg").write_bytes(b"x")
    assert len(calibration_images([str(tmp_path)])) == 210


def test_sdk_wrapper_follows_sample_api(tmp_path):
    calls = []

    class Quant:
        def save(self, file_dir, file_name):
            calls.append(("save", file_name))
            (Path(file_dir) / file_name).write_bytes(b"q")

    class Regular:
        def __init__(self, model):
            calls.append(("regular", model))

        def calibrate_torch(self, calibration_data):
            calls.append(("calibrate", list(calibration_data)))
            return {"t": 1}

        def quantize(self, thresholds):
            calls.append(("quantize", thresholds))
            return Quant()

    class QM:
        @staticmethod
        def load(path):
            calls.append(("load", Path(path).name))
            return "loaded"

    class Net:
        @staticmethod
        def from_quantized_model(q):
            calls.append(("network", q))
            return "net", None

    class Prog:
        @staticmethod
        def from_executable(executable, descriptions):
            calls.append(("program", executable, descriptions))
            return SimpleNamespace(to_file=lambda p: calls.append(("to_file", p)))

    def compile_(hardware_parameters, network, parameters):
        calls.append(("compile", hardware_parameters, network, parameters))
        return "exe", "desc"

    m = SimpleNamespace(
        compiler=SimpleNamespace(compile_=compile_),
        preliminary_model_conditioning=lambda model: model,
        RegularModel=Regular,
        QuantizedModel=QM,
        Network=Net,
        TpuProgram=Prog,
        TPU_H1M="H",
        O1="o1",
        O5="o5",
        __version__="1",
    )
    sdk = Sdk(m)
    assert sdk.condition("model") == "model" and sdk.version == "1"
    quant = sdk.quantize(sdk.calibrate("model", "images", "frames"))
    sdk.compile(quant, "O1", "x.tpu")
    assert calls == [
        ("regular", "model"),
        ("calibrate", ["images"]),
        ("quantize", {"t": 1}),
        ("save", "quant.qm.onnx"),
        ("load", "quant.qm.onnx"),
        ("network", "loaded"),
        ("compile", "H", "net", "o1"),
        ("program", "exe", "desc"),
        ("to_file", "x.tpu"),
    ]


def test_load_frames_stacks_one_tensor(tmp_path):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    paths = []
    for i in range(3):
        p = tmp_path / f"{i}.jpg"
        cv2.imwrite(str(p), np.full((20, 30, 3), 100, np.uint8))
        paths.append(p)
    frames = load_frames(paths, 16)
    assert frames.shape == (3, 3, 16, 16) and frames.dtype == np.float32
    with pytest.raises(ValueError):
        load_frames([tmp_path / "none.jpg"], 16)


def test_onnx_names_reads_ultralytics_metadata(tmp_path):
    pytest.importorskip("onnx")
    import onnx
    from onnx import TensorProto, helper

    from export.linq_compile import onnx_names

    graph = helper.make_graph(
        [],
        "g",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1])],
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1])],
    )
    model = helper.make_model(graph)
    entry = model.metadata_props.add()
    entry.key, entry.value = "names", "{0: 'person', 1: 'helmet'}"
    path = tmp_path / "m.onnx"
    onnx.save(model, path)
    assert onnx_names(str(path)) == {0: "person", 1: "helmet"}
    bare = tmp_path / "b.onnx"
    onnx.save(helper.make_model(graph), bare)
    assert onnx_names(str(bare)) == {}
