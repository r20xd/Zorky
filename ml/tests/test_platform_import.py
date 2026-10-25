import json

import cv2
import numpy as np
import pytest

from train.platform_import import (
    annotation_record,
    curl_batch,
    import_source,
    normalized_box,
    parse_export,
)

SPEC = {
    "url": "https://platform.ultralytics.com/test/datasets/masks",
    "source_classes": ["Coverall", "Mask"],
    "class_mapping": {0: None, 1: 3},
}


def item(boxes):
    return {
        "type": "image",
        "line_number": 2,
        "file": "../../outside.jpg",
        "url": "https://cdn.ul.run/example.jpg",
        "width": 40,
        "height": 30,
        "split": "train",
        "annotations": {"boxes": boxes},
    }


def write_export(path, image=None):
    header = {"type": "dataset", "task": "detect", "class_names": {"0": "Coverall", "1": "Mask"}}
    image = image or item([[1, 0.5, 0.5, 0.2, 0.2]])
    path.write_text(json.dumps(header) + "\n" + json.dumps(image) + "\n")


def test_small_rounding_overflow_is_clipped_but_real_error_is_rejected():
    c, box, clipped = normalized_box([1, 0.5, 0.49998, 1, 1], 2)
    assert c == 1 and clipped
    assert box[1] - box[3] / 2 >= 0
    for bad in ([1, 2, 2, 2, 2], [1, 0.9, 0.5, 0.8, 0.1], [1, float("nan"), 0.5, 1, 1]):
        with pytest.raises(ValueError):
            normalized_box(bad, 2)


def test_invalid_mask_quarantines_whole_image_even_with_valid_mask():
    record = annotation_record(item([[1, 2, 2, 2, 2], [1, 0.5, 0.5, 0.2, 0.2]]), SPEC)
    assert record["annotation_status"] == "quarantined_invalid_target_bbox"
    assert record["target_yolo_rows"] == ["3 0.50000000 0.50000000 0.20000000 0.20000000"]
    assert not record["eligible_for_training"]


def test_coverall_is_not_face_protection_and_empty_is_not_safe_negative():
    record = annotation_record(item([[0, 0.5, 0.5, 0.2, 0.2]]), SPEC)
    assert record["target_yolo_rows"] == []
    assert record["annotation_status"] == "unverified_negative_or_unlabelled"
    assert annotation_record(item([]), SPEC)["annotation_status"] == record["annotation_status"]


def test_invalid_auxiliary_class_does_not_become_target_label():
    record = annotation_record(item([[0, 2, 2, 2, 2], [1, 0.5, 0.5, 0.2, 0.2]]), SPEC)
    assert record["annotation_status"] == "source_target_labels_valid_partial_classes"
    assert record["invalid_boxes"][0]["target"] is False


def test_rejects_wrong_classes_before_downloading(tmp_path):
    export = tmp_path / "masks.ndjson"
    write_export(export)
    wrong = {**SPEC, "source_classes": ["person", "face"]}
    with pytest.raises(ValueError, match="source_classes"):
        parse_export(export, wrong)


def test_download_resume_ignores_source_filename_and_preserves_snapshot(tmp_path, monkeypatch):
    export, out = tmp_path / "masks.ndjson", tmp_path / "raw"
    write_export(export)
    calls = []

    def download(url, target):
        calls.append(url)
        cv2.imwrite(str(target), np.zeros((30, 40, 3), np.uint8))

    monkeypatch.setattr("train.platform_import.download_image", download)
    report = import_source(export, out, SPEC, workers=1)
    assert report["summary"]["downloaded_decoded"] == 1
    assert report["summary"]["image_errors"] == 0
    assert report["records"][0]["local_image"] == "images/000002.jpg"
    assert (out / "source.ndjson").read_bytes() == export.read_bytes()
    import_source(export, out, SPEC, workers=1)
    assert len(calls) == 1
    write_export(export, item([]))
    with pytest.raises(ValueError, match="snapshot_changed"):
        import_source(export, out, SPEC, workers=1)


def test_dimension_mismatch_is_reported(tmp_path, monkeypatch):
    export = tmp_path / "masks.ndjson"
    write_export(export)

    def download(url, target):
        cv2.imwrite(str(target), np.zeros((20, 20, 3), np.uint8))

    monkeypatch.setattr("train.platform_import.download_image", download)
    report = import_source(export, tmp_path / "out", SPEC, workers=1)
    assert report["summary"]["image_errors"] == 1
    assert report["records"][0]["image_error"] == "dimension_mismatch"


def test_curl_only_promotes_successful_transfers(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    good = images / "000002.jpg.part"
    failed = images / "000003.jpg.part"

    def run(command, stdout, **kwargs):
        assert "--http2" in command and "--fail" in command
        good.write_bytes(b"valid transport")
        failed.write_bytes(b"partial error")
        stdout.write(f"{good}\t0\t200\n{failed}\t22\t403\n")

    monkeypatch.setattr("train.platform_import.subprocess.run", run)
    curl_batch([item([]), {**item([]), "line_number": 3}], images, 4)
    assert good.with_suffix("").read_bytes() == b"valid transport"
    assert not failed.with_suffix("").exists()
    assert not (tmp_path / "download.curl").exists()


def test_curl_does_not_accept_arbitrary_download_hosts(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    with pytest.raises(ValueError, match="unsupported_image_host"):
        curl_batch([{**item([]), "url": "file:///private/file"}], images, 1)
