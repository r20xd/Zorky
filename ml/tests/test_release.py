import pytest

from train.release import compare_metrics


def example(value):
    return {
        "mAP50": value,
        "mAP50-95": value,
        "classes": {"cigarette": {"mAP50": value, "mAP50-95": value}},
    }


def test_compare_metrics_requires_matching_classes_and_class_quality():
    pt, onnx = example(0.8), example(0.795)
    assert compare_metrics(pt, onnx)["cigarette"]["mAP50"] == pytest.approx(0.005)
    onnx["classes"]["cigarette"]["mAP50"] = 0.7
    with pytest.raises(ValueError, match="превышает"):
        compare_metrics(pt, onnx)
    onnx["classes"] = {}
    with pytest.raises(ValueError, match="разные классы"):
        compare_metrics(pt, onnx)
