"""Аудит внешнего YOLO-набора перед разметкой и включением в дообучение.

Не создаёт train/val/test и не считает чужую неполную разметку полной.
Из ml/: python -m train.source_audit SOURCE --existing data/ppe --out REPORT.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import yaml

from .stats import IMAGE_SUFFIXES, valid_class


def image_info(path: Path) -> dict:
    """Проверяет декодирование и сохраняет SHA256 и pHash для поиска кандидатов-дублей."""
    raw = path.read_bytes()
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("изображение не декодируется")
    gray = cv2.cvtColor(cv2.resize(image, (32, 32)), cv2.COLOR_BGR2GRAY)
    low = cv2.dct(gray.astype(np.float32))[:8, :8].reshape(-1)
    bits = low > np.median(low[1:])
    phash = sum(int(bit) << i for i, bit in enumerate(bits))
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "phash": f"{phash:016x}",
        "width": int(image.shape[1]),
        "height": int(image.shape[0]),
    }


def source_records(root: Path, names: dict) -> list[dict]:
    records = []
    for split in ("train", "valid", "val", "test"):
        image_dir = root / split / "images"
        for path in sorted(image_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            record = {
                "image": path.relative_to(root).as_posix(),
                "source_split": split,
                "annotation_status": "source_labels_unreviewed",
                "eligible_for_training": False,
                "errors": [],
            }
            if path.is_symlink():
                record["errors"].append("symbolic_link")
                records.append(record)
                continue
            try:
                record.update(image_info(path))
            except (ValueError, OSError, cv2.error):
                record["errors"].append("unreadable_image")
            label = root / split / "labels" / path.relative_to(image_dir).with_suffix(".txt")
            classes = Counter()
            if not label.is_file() or label.is_symlink():
                record["errors"].append("missing_or_symlink_label")
            else:
                record["label_sha256"] = hashlib.sha256(label.read_bytes()).hexdigest()
                try:
                    for line in label.read_text(encoding="utf-8-sig").splitlines():
                        if not line.strip():
                            continue
                        class_id = valid_class(line, names)
                        if class_id is None:
                            record["errors"].append("invalid_label_row")
                        else:
                            classes[class_id] += 1
                except (OSError, UnicodeError):
                    record["errors"].append("unreadable_label")
            record["source_objects"] = dict(classes)
            records.append(record)
    return records


def existing_index(root: Path, workers: int = 8) -> dict[str, list[str]]:
    """Индекс точных совпадений со старым train/val; исходники не изменяются."""
    paths = sorted(
        p
        for p in (root / "images").rglob("*")
        if p.is_file() and not p.is_symlink() and p.suffix.lower() in IMAGE_SUFFIXES
    )

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest(), path.relative_to(root).as_posix()

    index = defaultdict(list)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for sha, name in pool.map(digest, paths):
            index[sha].append(name)
    return index


def audit(root: Path, existing: Path | None = None) -> dict:
    config = yaml.safe_load((root / "data.yaml").read_text(encoding="utf-8"))
    names = config["names"]
    names = dict(enumerate(names)) if isinstance(names, list) else names
    records = source_records(root, names)
    if not records:
        raise ValueError("не найдено изображений в <split>/images")
    old = existing_index(existing) if existing else {}
    exact, near = [], []
    valid = [r for r in records if "sha256" in r]
    for i, record in enumerate(valid):
        record["existing_exact_matches"] = old.get(record["sha256"], [])
        for previous in valid[:i]:
            if record["sha256"] == previous["sha256"]:
                exact.append([previous["image"], record["image"]])
            elif (int(record["phash"], 16) ^ int(previous["phash"], 16)).bit_count() <= 4:
                near.append([previous["image"], record["image"]])
    return {
        "source_classes": names,
        "images": len(records),
        "by_source_split": dict(Counter(r["source_split"] for r in records)),
        "images_with_errors": sum(bool(r["errors"]) for r in records),
        "existing_exact_images": sum(bool(r.get("existing_exact_matches")) for r in records),
        "exact_duplicate_pairs": exact,
        "near_duplicate_candidates": near,
        "near_duplicate_scope": "within source only; manual review required",
        "existing_comparison_scope": "file SHA256 only; reencoded/scene overlap not excluded",
        "annotation_status": "quarantined; full target-class annotation not verified",
        "records": records,
    }


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("--existing", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = audit(args.source, args.existing)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    keys = ("images", "by_source_split", "images_with_errors", "existing_exact_images")
    print(json.dumps({k: report[k] for k in keys}, ensure_ascii=False))
    print(
        f"Точных пар: {len(report['exact_duplicate_pairs'])}; "
        f"кандидатов близких: {len(report['near_duplicate_candidates'])}; отчёт: {args.out}"
    )
    if report["images_with_errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
