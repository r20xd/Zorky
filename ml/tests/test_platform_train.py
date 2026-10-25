import hashlib
import json

import pytest
import yaml

from train import platform_train


def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_train, "ROOT", tmp_path)
    root = tmp_path / "dataset"
    records = []
    for split in ("train", "val", "test"):
        image = root / "images" / split / f"helmet-{split}.jpg"
        label = root / "labels" / split / f"helmet-{split}.txt"
        image.parent.mkdir(parents=True, exist_ok=True)
        label.parent.mkdir(parents=True, exist_ok=True)
        raw = split.encode()
        image.write_bytes(raw)
        rows = ["1 0.5 0.5 0.2 0.2"]
        label.write_text(rows[0] + "\n")
        records.append(
            {
                "split": split,
                "group": split,
                "source": "helmet",
                "id": f"helmet-{split}",
                "image": "raw/example.jpg",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "rows": rows,
            }
        )
    (root / "manifest.json").write_text(json.dumps({"records": records, "summary": {}}))
    data = root / "data.yaml"
    data.write_text(yaml.safe_dump({"path": str(root), "names": platform_train.NAMES}))
    return root, data


def test_preflight_detects_modified_image(tmp_path, monkeypatch):
    root, data = prepared(tmp_path, monkeypatch)
    assert "manifest_sha256" in platform_train.validate_dataset(data)
    (root / "images/train/helmet-train.jpg").write_bytes(b"changed")
    with pytest.raises(ValueError, match="изменилось изображение"):
        platform_train.validate_dataset(data)


def test_preflight_detects_modified_label_and_extra_image(tmp_path, monkeypatch):
    root, data = prepared(tmp_path, monkeypatch)
    label = root / "labels/train/helmet-train.txt"
    label.write_text("4 0.5 0.5 0.2 0.2\n")
    with pytest.raises(ValueError, match="изменились метки"):
        platform_train.validate_dataset(data)
    label.write_text("1 0.5 0.5 0.2 0.2\n")
    (root / "images/train/extra.jpg").write_bytes(b"extra")
    with pytest.raises(ValueError, match="лишние"):
        platform_train.validate_dataset(data)


def test_preflight_rejects_group_leakage(tmp_path, monkeypatch):
    root, data = prepared(tmp_path, monkeypatch)
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["records"][1]["group"] = manifest["records"][0]["group"]
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="утечка"):
        platform_train.validate_dataset(data)


def test_cli_check_does_not_accept_output_path_in_name():
    with pytest.raises(SystemExit):
        platform_train.parse_args(["--name", "../../other", "--check"])
