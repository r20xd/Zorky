import cv2
import numpy as np
import pytest

from eval import robustness as rb


def test_distortions_keep_shape_and_change_image():
    rng = np.random.default_rng(1)
    img = rng.integers(0, 255, (64, 80, 3), dtype=np.uint8)
    for name, fn in rb.DISTORTIONS.items():
        out = fn(img)
        assert out.shape == img.shape and out.dtype == np.uint8, name
        assert not np.array_equal(out, img), name
    assert rb.dark(img).mean() < img.mean() < rb.fog(img).mean() + 60


def test_drops_worst_and_table():
    cls = lambda a, b: {"mAP50": a, "mAP50-95": b}  # noqa: E731
    res = {
        "original": {"mAP50": 0.9, "mAP50-95": 0.5, "classes": {"helmet": cls(0.9, 0.5)}},
        "dark": {"mAP50": 0.5, "mAP50-95": 0.2, "classes": {"helmet": cls(0.5, 0.2)}},
        "fog": {"mAP50": 0.8, "mAP50-95": 0.45, "classes": {"helmet": cls(0.8, 0.45)}},
    }
    assert rb.worst(res)[0][0] == "dark"
    assert rb.drops(res)["fog"]["classes"]["helmet"]["mAP50"] == pytest.approx(-0.1)
    t = rb.format_table(res)
    assert "helmet 50-95" in t and t.count("\n") == 4


def test_tiny_model_two_images(tmp_path):
    pytest.importorskip("ultralytics")
    from ultralytics import YOLO

    src = tmp_path / "src"
    (src / "images/val").mkdir(parents=True)
    (src / "labels/val").mkdir(parents=True)
    rng = np.random.default_rng(2)
    for i in range(2):
        cv2.imwrite(
            str(src / f"images/val/{i}.jpg"), rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)
        )
        (src / f"labels/val/{i}.txt").write_text("1 0.5 0.5 0.4 0.4\n")
    model = YOLO("yolov8n.yaml")  # случайные веса, без загрузки
    y = rb.make_distorted_set(src, tmp_path / "d", "fog")
    assert len(list((tmp_path / "d/images/val").iterdir())) == 2
    m = rb.val_metrics(model, y, 64, "cpu")
    assert 0.0 <= m["mAP50"] <= 1.0 and "mAP50-95" in m
