"""Сборка датасета «каска / голова» в формате YOLO (ML-1, T1).

    python ml/train/prepare_data.py                 # скачать keremberke/hard-hat-detection целиком
    python ml/train/prepare_data.py --mini          # полный train + valid-mini
    python ml/train/prepare_data.py --limit 20      # только 20 картинок на часть (пробное обучение)
    python ml/train/prepare_data.py --zips-dir DIR  # zip-и уже скачаны вручную

Источник — zip-архивы с COCO-разметкой (`_annotations.coco.json`) на Hugging Face, без логина.
Классы источника hardhat/no-hardhat приводятся к `data.yaml`: helmet=1, head=2.
Результат — `ml/data/ppe/` (вне git).
Из stdlib: ничего ставить не нужно.
"""

from __future__ import annotations

import argparse
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ML = Path(__file__).resolve().parents[1]
DATA_DIR = ML / "data" / "ppe"
HF_BASE = "https://huggingface.co/datasets/keremberke/hard-hat-detection/resolve/main/data"
# часть набора на диске -> часть YOLO (ultralytics ждёт train/val)
SPLITS = {"train": "train", "valid": "val"}
# имя класса в источнике -> id в data.yaml
CLASS_IDS = {"person": 0, "helmet": 1, "head": 2, "hardhat": 1, "no-hardhat": 2}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def coco_to_yolo_lines(
    annotations: list[dict], categories: dict[int, str], width: int, height: int
) -> list[str]:
    """COCO [x, y, w, h] в пикселях -> YOLO `cls cx cy w h` в 0..1; чужие классы пропускаем."""
    lines = []
    for ann in annotations:
        cls = CLASS_IDS.get(categories.get(ann["category_id"], "").lower())
        if cls is None:
            continue
        x, y, w, h = ann["bbox"]
        x1, y1 = max(x, 0.0), max(y, 0.0)
        x2, y2 = min(x + w, width), min(y + h, height)
        if x2 <= x1 or y2 <= y1:
            continue
        cx, cy = (x1 + x2) / 2 / width, (y1 + y2) / 2 / height
        lines.append(f"{cls} {cx:.6f} {cy:.6f} {(x2 - x1) / width:.6f} {(y2 - y1) / height:.6f}")
    return lines


def convert_coco(coco: dict, src_images: Path, out: Path, limit: int | None = None) -> int:
    """Раскладывает картинки и метки в out/images, out/labels. Возвращает число картинок."""
    categories = {c["id"]: c["name"] for c in coco["categories"]}
    by_image: dict[int, list[dict]] = {}
    for ann in coco["annotations"]:
        by_image.setdefault(ann["image_id"], []).append(ann)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    count = 0
    for image in coco["images"]:
        if limit is not None and count >= limit:
            break
        src = src_images / PurePosixPath(image["file_name"]).name
        if not src.is_file():
            continue
        lines = coco_to_yolo_lines(
            by_image.get(image["id"], []), categories, image["width"], image["height"]
        )
        shutil.copyfile(src, out / "images" / src.name)
        (out / "labels" / f"{src.stem}.txt").write_text("\n".join(lines), encoding="utf-8")
        count += 1
    return count


def convert_zip(zip_path: Path, out: Path, limit: int | None = None) -> int:
    tmp = out.parent / f".tmp_{out.name}"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(tmp)
        ann_files = list(tmp.rglob("_annotations.coco.json"))
        if not ann_files:
            raise SystemExit(f"{zip_path.name}: нет _annotations.coco.json")
        coco = json.loads(ann_files[0].read_text(encoding="utf-8"))
        return convert_coco(coco, ann_files[0].parent, out, limit)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"скачиваю {url}")
    partial = dest.with_suffix(".zip.part")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, partial.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
        partial.replace(dest)
    finally:
        partial.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Датасет каска/голова -> YOLO")
    parser.add_argument("--out", type=Path, default=DATA_DIR)
    parser.add_argument("--zips-dir", type=Path, help="папка с уже скачанными train.zip, valid.zip")
    parser.add_argument(
        "--mini", action="store_true", help="использовать valid-mini (train-mini нет)"
    )
    parser.add_argument("--limit", type=int, help="не больше N картинок на часть")
    args = parser.parse_args()

    zips_dir = args.zips_dir or args.out.parent / "_download"
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit должен быть больше нуля")
    shutil.rmtree(args.out, ignore_errors=True)
    for source, split in SPLITS.items():
        suffix = "-mini" if args.mini and source == "valid" else ""
        zip_path = zips_dir / f"{source}{suffix}.zip"
        if not zip_path.is_file():
            download(f"{HF_BASE}/{zip_path.name}", zip_path)
        n = convert_zip(zip_path, args.out / "_split" / split, args.limit)
        print(f"{split}: {n} картинок")
    # ultralytics ждёт images/<split> и labels/<split>
    for split in SPLITS.values():
        part = args.out / "_split" / split
        for kind in ("images", "labels"):
            target = args.out / kind / split
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(part / kind, target)
    shutil.rmtree(args.out / "_split")
    print(f"готово: {args.out}")


if __name__ == "__main__":
    main()
