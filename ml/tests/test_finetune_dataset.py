import json
from copy import deepcopy

import pytest

from train.finetune_dataset import build, classes, digest, local_path, validate_plan


def fixture_plan(tmp_path):
    old = tmp_path / "old"
    indexed = []
    for split, name, cls in (("train", "rare", 4), ("train", "helmet", 1), ("val", "held", 3)):
        image = old / "images" / split / f"{name}.png"
        label = old / "labels" / split / f"{name}.txt"
        image.parent.mkdir(parents=True, exist_ok=True)
        label.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(name.encode())
        label.write_text(f"{cls} 0.5 0.5 0.2 0.2\n")
        indexed.append({"image": image.relative_to(old).as_posix(), "sha256": digest(image)})
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"records": indexed}))
    records = []
    for split in ("train", "val", "test"):
        image = tmp_path / f"new-{split}.png"
        image.write_bytes(split.encode())
        records.append(
            {
                "id": split,
                "image": image.name,
                "sha256": digest(image),
                "group": split,
                "split": split,
                "yolo_rows": [f"{c} 0.5 0.5 0.2 0.2" for c in (1, 2, 3, 4)],
            }
        )
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "seed": 42,
                "replay_images": 2,
                "existing_index_sha256": digest(index),
                "records": records,
            }
        )
    )
    return old, index, plan, records


def test_build_uses_train_only_and_does_not_overwrite(tmp_path):
    old, index, plan, _ = fixture_plan(tmp_path)
    before = digest(old / "images/train/rare.png")
    out = tmp_path / "dataset"
    report = build(plan, index, old, out, root=tmp_path)
    assert report["summary"]["train"]["images"] == 3
    replay = [r for r in report["records"] if r.get("source") == "old_train_replay"]
    assert {r["image"] for r in replay} == {"images/train/rare.png", "images/train/helmet.png"}
    assert all(r["split"] == "train" for r in replay)
    assert digest(old / "images/train/rare.png") == before
    assert (out / "labels/test/test.txt").is_file()
    with pytest.raises(FileExistsError):
        build(plan, index, old, out, root=tmp_path)


@pytest.mark.parametrize("key", ["group", "sha256"])
def test_rejects_cross_split_leakage(tmp_path, key):
    _, _, _, records = fixture_plan(tmp_path)
    broken = deepcopy(records)
    broken[1][key] = broken[0][key]
    with pytest.raises(ValueError, match="утечка"):
        validate_plan(broken)


def test_rejects_changed_source_before_export(tmp_path):
    old, index, plan, records = fixture_plan(tmp_path)
    (tmp_path / records[0]["image"]).write_bytes(b"changed")
    out = tmp_path / "dataset"
    with pytest.raises(ValueError, match="изменилось изображение"):
        build(plan, index, old, out, root=tmp_path)
    assert not out.exists()


def test_rejects_path_escape_and_invalid_annotations(tmp_path):
    with pytest.raises(ValueError, match="пределами"):
        local_path(tmp_path, "../other/image.png")
    for row in ("3 nan 0.5 0.2 0.2", "0 0.5 0.5 0.2 0.2", "4 0.99 0.5 0.5 0.2"):
        with pytest.raises(ValueError, match="bbox"):
            classes([row])
