"""SHA256-манифест полного набора и поиск точных train/val совпадений."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from .stats import IMAGE_SUFFIXES, split_stats

NAMES = {0: "person", 1: "helmet", 2: "head", 3: "respirator", 4: "cigarette"}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit(root):
    records, hashes = [], defaultdict(list)
    for split in ("train", "val"):
        image_dir = root / "images" / split
        if not image_dir.is_dir():
            raise ValueError(f"Нет каталога: {image_dir}")
        for image in sorted(image_dir.rglob("*")):
            if image.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            relative = image.relative_to(image_dir)
            label = root / "labels" / split / relative.with_suffix(".txt")
            record = {
                "split": split,
                "image": image.relative_to(root).as_posix(),
                "image_sha256": sha256(image),
                "label": label.relative_to(root).as_posix(),
                "label_sha256": sha256(label) if label.is_file() else None,
            }
            records.append(record)
            hashes[record["image_sha256"]].append(record)
    overlap = [
        {"sha256": key, "images": [r["image"] for r in group]}
        for key, group in sorted(hashes.items())
        if {r["split"] for r in group} == {"train", "val"}
    ]
    return {
        "classes": NAMES,
        "stats": {split: split_stats(root, split, NAMES) for split in ("train", "val")},
        "cross_split_duplicates": overlap,
        "files": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data", type=Path, default=Path(__file__).resolve().parents[1] / "data/ppe"
    )
    parser.add_argument("--out", type=Path, help="манифест, по умолчанию внутри data/ppe")
    args = parser.parse_args()
    report = audit(args.data)
    out = args.out or args.data / "audit.manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Манифест: {out}; SHA256: {sha256(out)}")
    print(
        f"Изображений: {len(report['files'])}; train/val совпадений: "
        f"{len(report['cross_split_duplicates'])}"
    )
    invalid = any(r["broken"] or r["missing"] or r["orphan"] for r in report["stats"].values())
    if report["cross_split_duplicates"] or invalid:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
