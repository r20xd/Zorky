import hashlib

from train.audit import audit


def test_manifest_detects_cross_split_content_even_with_different_names(tmp_path):
    for split, name in (("train", "a.jpg"), ("val", "renamed.jpg")):
        images = tmp_path / "images" / split
        labels = tmp_path / "labels" / split
        images.mkdir(parents=True)
        labels.mkdir(parents=True)
        (images / name).write_bytes(b"same-image")
        (labels / name.replace(".jpg", ".txt")).write_text("1 .5 .5 .2 .2")
    report = audit(tmp_path)
    assert len(report["cross_split_duplicates"]) == 1
    assert report["files"][0]["image_sha256"] == hashlib.sha256(b"same-image").hexdigest()
    assert report["files"][0]["label_sha256"] == hashlib.sha256(b"1 .5 .5 .2 .2").hexdigest()
    (tmp_path / "images/val/renamed.jpg").write_bytes(b"different-image")
    assert audit(tmp_path)["cross_split_duplicates"] == []


def test_expected_class_mapping_matches_training_config():
    from pathlib import Path

    import yaml

    from train.audit import NAMES

    config = Path(__file__).resolve().parents[1] / "train/data.yaml"
    assert yaml.safe_load(config.read_text())["names"] == NAMES
