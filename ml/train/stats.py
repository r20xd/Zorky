"""Считает изображения и валидные YOLO-объекты; веса и torch не нужны."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import yaml

ML = Path(__file__).resolve().parents[1]
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def valid_class(line, class_ids):
    parts = line.split()
    if len(parts) != 5:
        return None
    try:
        class_id = int(parts[0])
        cx, cy, width, height = map(float, parts[1:])
    except ValueError:
        return None
    if class_id not in class_ids:
        return None
    if not all(math.isfinite(v) and 0 <= v <= 1 for v in (cx, cy, width, height)):
        return None
    if width <= 0 or height <= 0:
        return None
    if (
        cx - width / 2 < -1e-6
        or cy - height / 2 < -1e-6
        or cx + width / 2 > 1 + 1e-6
        or cy + height / 2 > 1 + 1e-6
    ):
        return None
    return class_id


def split_stats(root, split, names):
    image_dir, label_dir = root / "images" / split, root / "labels" / split
    images = sorted(p for p in image_dir.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)
    result = {
        "images": len(images),
        "empty": 0,
        "missing": 0,
        "broken": 0,
        "invalid_rows": 0,
        "orphan": 0,
        "classes": {i: {"images": 0, "objects": 0} for i in names},
    }
    expected_labels = set()
    for image in images:
        label = label_dir / image.relative_to(image_dir).with_suffix(".txt")
        expected_labels.add(label)
        if not label.is_file():
            result["missing"] += 1
            continue
        try:
            lines = [
                line for line in label.read_text(encoding="utf-8-sig").splitlines() if line.strip()
            ]
        except (OSError, UnicodeError):
            result["broken"] += 1
            continue
        if not lines:
            result["empty"] += 1
        seen = set()
        broken = False
        for line in lines:
            class_id = valid_class(line, names)
            if class_id is None:
                broken = True
                result["invalid_rows"] += 1
                continue
            result["classes"][class_id]["objects"] += 1
            seen.add(class_id)
        result["broken"] += broken
        for class_id in seen:
            result["classes"][class_id]["images"] += 1
    result["orphan"] = len(set(label_dir.rglob("*.txt")) - expected_labels)
    return result


def report(root, names):
    print(
        "| Часть | Картинки | Пустые метки | Нет метки | Битые метки | "
        "Битые строки | Метки без картинки |"
    )
    print("|---|---:|---:|---:|---:|---:|---:|")
    results = {split: split_stats(root, split, names) for split in ("train", "val")}
    for split, result in results.items():
        values = [
            result[k] for k in ("images", "empty", "missing", "broken", "invalid_rows", "orphan")
        ]
        print(f"| {split} | " + " | ".join(map(str, values)) + " |")
    print("\n| Часть | Класс | Картинки с классом | Валидные объекты |")
    print("|---|---|---:|---:|")
    for split, result in results.items():
        for class_id, name in names.items():
            counts = result["classes"][class_id]
            print(f"| {split} | {class_id}: {name} | {counts['images']} | {counts['objects']} |")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ML / "data" / "ppe")
    args = parser.parse_args()
    for split in ("train", "val"):
        if not (args.data / "images" / split).is_dir():
            parser.error(f"Нет каталога изображений {split}: {args.data}")
    names = yaml.safe_load(Path(__file__).with_name("data.yaml").read_text(encoding="utf-8"))[
        "names"
    ]
    results = report(args.data, names)
    if any(r["broken"] or r["missing"] or r["orphan"] for r in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
