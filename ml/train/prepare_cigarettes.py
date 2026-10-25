"""CigDet (CC BY 4.0): сигареты YOLO 0 → общий класс 4, без удаления других данных."""

from __future__ import annotations

import argparse
import math
import zipfile
from pathlib import Path, PurePosixPath

if __package__:
    from .prepare_respirators import DATA_DIR, RemoteZip
else:
    from prepare_respirators import DATA_DIR, RemoteZip

# Публичное файловое хранилище CigDet, DOI 10.17632/6hyrr8typ7.1.
SOURCE = (
    "https://prod-dcd-datasets-public-files-eu-west-1.s3.eu-west-1.amazonaws.com/"
    "950cd489-55b8-4a8f-af72-a764146048bd"
)


def remap_labels(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 5 or parts[0] != "0":
            raise ValueError(f"Ожидалась YOLO-разметка единственного класса CigDet: {line}")
        cx, cy, width, height = map(float, parts[1:])
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in (cx, cy, width, height)):
            raise ValueError(f"Координаты вне 0..1: {line}")
        x1, y1 = max(0, cx - width / 2), max(0, cy - height / 2)
        x2, y2 = min(1, cx + width / 2), min(1, cy + height / 2)
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"Пустой bbox: {line}")
        lines.append(f"4 {(x1 + x2) / 2:.6f} {(y1 + y2) / 2:.6f} {x2 - x1:.6f} {y2 - y1:.6f}")
    return "\n".join(lines)


def merge(archive, out, limit=None):
    counts = {}
    for source_split, target_split in (("train", "train"), ("test", "val")):
        counts[target_split] = 0
        for name in sorted(archive.namelist()):
            path = PurePosixPath(name)
            if path.parent.name != source_split or path.suffix.lower() not in (".jpg", ".png"):
                continue
            if limit is not None and counts[target_split] >= limit:
                break
            label_name = path.with_suffix(".txt").as_posix()
            label = remap_labels(archive.read(label_name).decode("utf-8-sig"))
            image_dir, labels_dir = out / "images" / target_split, out / "labels" / target_split
            image_dir.mkdir(parents=True, exist_ok=True)
            labels_dir.mkdir(parents=True, exist_ok=True)
            target = image_dir / f"cigdet_{path.name}"
            if not target.exists():
                partial = target.with_suffix(target.suffix + ".part")
                try:
                    partial.write_bytes(archive.read(name))
                    partial.replace(target)
                finally:
                    partial.unlink(missing_ok=True)
            (labels_dir / f"cigdet_{path.stem}.txt").write_text(label, encoding="utf-8")
            counts[target_split] += 1
            print(f"{target_split}: {path.name}", flush=True)
        if counts[target_split] == 0:
            raise ValueError(f"Нет изображений CigDet: {source_split}")
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-zip", type=Path)
    parser.add_argument("--out", type=Path, default=DATA_DIR)
    parser.add_argument("--limit", type=int, help="картинок на часть; без флага — весь CigDet")
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit должен быть больше нуля")
    print("Открываю CigDet ZIP (HTTP Range для удалённого источника)", flush=True)
    with zipfile.ZipFile(args.source_zip or RemoteZip(SOURCE)) as archive:
        print(merge(archive, args.out, args.limit))


if __name__ == "__main__":
    main()
