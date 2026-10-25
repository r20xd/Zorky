import json
import sys
import types

import numpy as np
import pytest

from export import linq_bench as lb
from tests.test_infer import FakePytpu

NB = lb.__file__.replace("linq_bench.py", "linq_bench.ipynb")


class Pytpu(FakePytpu):
    """Подмена pytpu: выход по виду программы (pose — 56 каналов, иначе 84)."""

    def load(self, path):
        self.path = path
        return self

    def run(self, feeds):
        channels = 56 if "pose" in self.path else 84
        return {"head": np.zeros((1, channels, 8400), dtype=np.float32)}


def _programs(folder):
    folder.mkdir()
    for role in lb.ROLES:
        (folder / f"{role}.tpu").write_bytes(b"")
        (folder / f"{role}.tpu.json").write_text(json.dumps({"input": "in0", "outputs": ["head"]}))


def test_find_programs_needs_pair(tmp_path):
    _programs(tmp_path / "p")
    (tmp_path / "p" / "pose.tpu.json").unlink()
    assert sorted(lb.find_programs(tmp_path / "p")) == ["people", "ppe"]


def test_capacity_and_kind():
    assert lb.capacity(23.0, 5) == 4
    assert lb.program_kind("pose", {}) == "pose"
    assert lb.program_kind("ppe", {"kind": "obb"}) == "obb"
    assert lb.program_kind("people", {}) == "detect"


def test_jupyter_login_env_and_prompt():
    assert lb.jupyter_login({"LINQ_JUPYTER_USER": "u", "LINQ_JUPYTER_PASSWORD": "p"}) == ("u", "p")
    assert lb.jupyter_login({}, ask=lambda q: "x") == ("x", "x")


def test_upload_result_sends_token():
    seen = {}

    class Resp:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(request, timeout):
        seen["auth"] = request.get_header("Authorization")
        seen["url"] = request.full_url
        return Resp()

    assert lb.upload_result("http://h/", "a.json", b"{}", "tok", opener) == 201
    assert seen == {"auth": "token tok", "url": "http://h/api/contents/a.json"}


def test_notebook_has_no_secrets_and_runs(tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    nb = json.loads(open(NB, encoding="utf-8").read())
    code = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
    assert all("password=" not in c.lower() for c in code)
    _programs(tmp_path / "packages")
    (tmp_path / "val").mkdir()
    cv2.imwrite(str(tmp_path / "val" / "a.jpg"), np.zeros((120, 160, 3), np.uint8))
    (tmp_path / "videos").mkdir()
    writer = cv2.VideoWriter(
        str(tmp_path / "videos" / "v.avi"), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 120)
    )
    for _ in range(5):
        writer.write(np.zeros((120, 160, 3), np.uint8))
    writer.release()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "pytpu", Pytpu())
    display = types.ModuleType("IPython.display")
    display.FileLink = lambda p: p
    display.display = lambda *a: None
    monkeypatch.setitem(sys.modules, "IPython", types.ModuleType("IPython"))
    monkeypatch.setitem(sys.modules, "IPython.display", display)
    for var in ("LINQ_JUPYTER_URL", "LINQ_FP32_PPE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LINQ_FRAMES", "3")
    ns = {"__name__": "nb"}
    for cell in code:
        exec(compile(cell, "nb", "exec"), ns)  # noqa: S102
    out = json.loads((tmp_path / "linq_bench_result.json").read_text(encoding="utf-8"))
    assert set(out["single"]) == {"ppe", "people", "pose"}
    assert [r["streams"] for r in out["combined"]] == [1, 2, 4]
    assert out["combined"][0]["infer"]["p95_ms"] >= 0
    assert out["int8_vs_fp32"]["skipped"]
    assert out["worker"]["skipped"] is None and out["worker"]["events"] == 0
    assert (tmp_path / "events.json").read_text(encoding="utf-8").strip() == "[]"


def test_compare_int8_fp32_agreement(tmp_path):
    img = tmp_path / "images" / "a.jpg"
    img.parent.mkdir()
    (tmp_path / "labels").mkdir()
    (tmp_path / "labels" / "a.txt").write_text("0 0.5 0.5 0.25 0.25\n")

    class Session:
        def __init__(self, box):
            self.box = box

        def get_inputs(self):
            return [types.SimpleNamespace(name="i")]

        def run(self, _o, feeds):
            out = np.zeros((1, 5, 10), dtype=np.float32)
            out[0, :4, 0] = self.box
            out[0, 4, 0] = 0.9
            return [out]

    box = [320, 320, 160, 160]  # квадрат 640 -> центр кадра
    res = lb.compare_int8_fp32(
        Session(box), Session(box), [img], load_image=lambda p: np.zeros((640, 640, 3), np.uint8)
    )
    assert res["agreement"]["f1"] == 1.0
    assert res["int8"]["recall"] == 1.0 and res["labeled_frames"] == 1
