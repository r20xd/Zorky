import json

import numpy as np

from train.platform_prepare import Groups, hamming, status, write_json


def test_transitive_groups_cannot_split_duplicate_chain():
    groups = Groups(4)
    groups.union(0, 1)
    groups.union(1, 2)
    assert groups.find(0) == groups.find(2)
    assert groups.find(0) != groups.find(3)


def test_hamming_identifies_actual_bit_distance():
    distances = hamming(np.array([0, 1, 15, 255], dtype=np.uint64), 1)
    assert distances.tolist() == [1, 0, 3, 7]


def test_status_preserves_previous_phase_metadata(tmp_path):
    status(tmp_path, "training", epoch=2, epochs=20)
    status(tmp_path, "exporting_onnx", weights="best.pt")
    record = json.loads((tmp_path / "status.json").read_text())
    assert record["phase"] == "exporting_onnx"
    assert record["epoch"] == 2 and record["weights"] == "best.pt"
    assert not (tmp_path / "status.json.tmp").exists()


def test_json_report_rejects_nan_metrics(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        write_json(tmp_path / "metrics.json", {"AP": float("nan")})
    assert not (tmp_path / "metrics.json").exists()
