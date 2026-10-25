import zipfile
from pathlib import PurePosixPath, PureWindowsPath

import pytest

from train import prepare_cigarettes
from train.prepare_cigarettes import merge, remap_labels


def test_single_class_is_remapped_and_bbox_clipped():
    assert remap_labels("0 0.5 0.5 0.2 0.4\n") == "4 0.500000 0.500000 0.200000 0.400000"
    assert remap_labels("0 0.95 0.5 0.2 0.4").endswith("0.150000 0.400000")


@pytest.mark.parametrize(
    "text", ["1 0.5 0.5 0.2 0.4", "0 nan 0.5 0.2 0.4", "0 0.5 0.5 0 0.4", "0 1.5 0.5 0.2 0.4"]
)
def test_rejects_unknown_classes_and_invalid_boxes(text):
    with pytest.raises(ValueError):
        remap_labels(text)


@pytest.mark.parametrize("archive_path", [PurePosixPath, PureWindowsPath])
def test_merge_uses_original_splits_preserves_other_datasets_and_is_repeatable(
    tmp_path, monkeypatch, archive_path
):
    # PureWindowsPath воспроизводит обратные слеши str(Path) даже при запуске на Linux.
    monkeypatch.setattr(prepare_cigarettes, "PurePosixPath", archive_path)
    out = tmp_path / "ppe"
    original = out / "labels/train/pped_original.txt"
    original.parent.mkdir(parents=True)
    original.write_text("3 0.5 0.5 0.2 0.2")
    source = tmp_path / "cigdet.zip"
    with zipfile.ZipFile(source, "w") as archive:
        for split in ("train", "test"):
            for index in range(3):
                archive.writestr(f"CigDet_dataset/{split}/smoking_{index}.jpg", b"image")
                archive.writestr(f"CigDet_dataset/{split}/smoking_{index}.txt", "0 0.5 0.5 0.2 0.4")
    for _ in range(2):
        with zipfile.ZipFile(source) as archive:
            assert merge(archive, out, limit=2) == {"train": 2, "val": 2}
    assert original.read_text().startswith("3 ")
    assert len(list((out / "images").rglob("*.jpg"))) == 4
    assert (out / "labels/val/cigdet_smoking_0.txt").read_text().startswith("4 ")
