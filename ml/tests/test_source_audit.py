import shutil

import cv2
import numpy as np
import pytest
import yaml

from train.source_audit import audit, image_info


def sample(root, split, name, image, label="1 0.5 0.5 0.4 0.4\n"):
    images, labels = root / split / "images", root / split / "labels"
    images.mkdir(parents=True, exist_ok=True)
    labels.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(images / f"{name}.png"), image)
    (labels / f"{name}.txt").write_text(label)
    return images / f"{name}.png"


def test_source_audit_quarantines_labels_and_finds_cross_split_duplicates(tmp_path):
    src, old = tmp_path / "source", tmp_path / "existing"
    image = np.random.default_rng(7).integers(0, 255, (80, 96, 3), dtype=np.uint8)
    original = sample(src, "train", "one", image)
    sample(src, "test", "copy", image)
    (src / "data.yaml").write_text(yaml.safe_dump({"names": ["Person", "Hardhat", "Vest"]}))
    (old / "images" / "val").mkdir(parents=True)
    shutil.copyfile(original, old / "images" / "val" / "old.png")
    report = audit(src, old)
    assert report["images"] == 2 and report["images_with_errors"] == 0
    assert report["existing_exact_images"] == 2
    assert len(report["exact_duplicate_pairs"]) == 1
    assert all(not r["eligible_for_training"] for r in report["records"])
    assert all(r["source_objects"] == {1: 1} for r in report["records"])


def test_source_audit_flags_unknown_class_and_broken_image(tmp_path):
    (tmp_path / "data.yaml").write_text("names: {0: Person}\n")
    image = np.zeros((40, 40, 3), np.uint8)
    path = sample(tmp_path, "valid", "broken", image, "7 0.5 0.5 0.4 0.4\n")
    path.write_bytes(b"not an image")
    report = audit(tmp_path)
    assert report["images_with_errors"] == 1
    assert report["records"][0]["errors"] == ["unreadable_image", "invalid_label_row"]


def test_phash_finds_reencoding_candidates_without_claiming_exact_match(tmp_path):
    image = np.random.default_rng(1).integers(0, 255, (100, 100, 3), dtype=np.uint8)
    a = tmp_path / "a.png"
    b = tmp_path / "b.jpg"
    cv2.imwrite(str(a), image)
    cv2.imwrite(str(b), image, [cv2.IMWRITE_JPEG_QUALITY, 98])
    left, right = image_info(a), image_info(b)
    assert left["sha256"] != right["sha256"]
    assert (int(left["phash"], 16) ^ int(right["phash"], 16)).bit_count() <= 4


def test_source_audit_rejects_empty_dataset(tmp_path):
    (tmp_path / "data.yaml").write_text("names: {0: Person}\n")
    with pytest.raises(ValueError, match="не найдено"):
        audit(tmp_path)
