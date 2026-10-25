"""Подготовка наборов Ultralytics: аудит дубликатов, группы и train/val/test.

Обучение и фоновые процессы не запускает.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import yaml

from .finetune_dataset import classes
from .source_audit import image_info
from .stats import IMAGE_SUFFIXES

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "ml/data/finetune-v2/raw"
BASE = ROOT / "ml/runs/release-ppe-s/best.pt"
BASE_SHA = "d151b99336e3765aa2b3c388fe8998609051287054266f50e199bbcd715276c8"
NAMES = {0: "person", 1: "helmet", 2: "head", 3: "face_protection", 4: "cigarette"}
SOURCES = ("helmet", "cigarette", "fangchen")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def status(run, phase, **extra):
    path = run / "status.json"
    previous = json.loads(path.read_text()) if path.exists() else {}
    value = {
        **previous,
        "phase": phase,
        "pid": os.getpid(),
        "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **extra,
    }
    write_json(path, value)
    print(value["updated_utc"], phase, extra, flush=True)


class Groups:
    def __init__(self, size):
        self.parents = list(range(size))

    def find(self, i):
        while self.parents[i] != i:
            self.parents[i] = self.parents[self.parents[i]]
            i = self.parents[i]
        return i

    def union(self, a, b):
        self.parents[self.find(b)] = self.find(a)


def hamming(values, value):
    xor = values ^ np.uint64(value)
    if hasattr(np, "bitwise_count"):
        return np.bitwise_count(xor)
    return np.fromiter((int(x).bit_count() for x in xor), dtype=np.uint8)


def prepare(run):
    destination = run / "dataset"
    manifest_path = destination / "manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text())
    if destination.exists():
        raise ValueError("incomplete dataset directory; inspect before using a new run directory")
    records, exports = [], {}
    for source in SOURCES:
        report = json.loads((RAW / source / "audit.json").read_text())
        if report["summary"]["image_errors"]:
            raise ValueError(f"image download/decode errors in {source}")
        exports[source] = report["export_sha256"]
        if sha(RAW / source / "source.ndjson") != exports[source]:
            raise ValueError(f"changed source export: {source}")
        for r in report["records"]:
            if r["annotation_status"] != "source_target_labels_valid_partial_classes":
                continue
            image = RAW / source / r["local_image"]
            if sha(image) != r["sha256"]:
                raise ValueError(f"changed raw image: {source}/{r['line_number']}")
            classes(r["target_yolo_rows"])
            records.append(
                {
                    "source": source,
                    "id": f"{source}-{r['line_number']:06d}",
                    "image": image.relative_to(ROOT).as_posix(),
                    "original_file": r["file"],
                    "sha256": r["sha256"],
                    "phash": r["phash"],
                    "rows": r["target_yolo_rows"],
                }
            )
    status(run, "indexing_existing_data", new_valid_images=len(records))
    old_path = run / "existing-index.json"
    if old_path.exists():
        old = json.loads(old_path.read_text())
    else:
        old_root = ROOT / "ml/data/ppe"
        paths = sorted(
            p
            for p in (old_root / "images").rglob("*")
            if p.is_file() and not p.is_symlink() and p.suffix.lower() in IMAGE_SUFFIXES
        )

        def feature(p):
            return {"split": p.relative_to(old_root).parts[1], **image_info(p)}

        with ThreadPoolExecutor(max_workers=4) as pool:
            old = list(pool.map(feature, paths))
        if not old:
            raise ValueError("missing old dataset index")
        write_json(old_path, old)
    status(run, "grouping_and_deduplicating", existing_images=len(old))
    old_phash = np.array([int(r["phash"], 16) for r in old], dtype=np.uint64)
    groups = Groups(len(records))
    seen_names, seen_hashes = {}, {}
    hashes = np.array([int(r["phash"], 16) for r in records], dtype=np.uint64)
    for i, r in enumerate(records):
        # Сохраняем вместе производные Roboflow одного исходного имени.
        name = r["source"] + ":" + Path(r["original_file"]).stem.split(".rf.")[0]
        for registry, key in ((seen_names, name), (seen_hashes, r["sha256"])):
            if key in registry:
                groups.union(i, registry[key])
            registry[key] = i
        for j in np.flatnonzero(hamming(hashes[:i], int(hashes[i])) <= 4):
            groups.union(i, int(j))
        matches = np.flatnonzero(hamming(old_phash, int(hashes[i])) <= 4)
        r["old_train_match"] = any(old[int(j)]["split"] == "train" for j in matches)
        r["old_val_match"] = any(old[int(j)]["split"] == "val" for j in matches)
    blocked = {groups.find(i) for i, r in enumerate(records) if r["old_val_match"]}
    forced_train = {groups.find(i) for i, r in enumerate(records) if r["old_train_match"]}
    assigned, selected, unique = {}, [], set()
    rng = random.Random(42)
    excluded = Counter()
    for i, r in enumerate(records):
        group = groups.find(i)
        if group in blocked:
            excluded["old_val_related"] += 1
            continue
        key = (r["source"], r["sha256"])
        if key in unique:
            excluded["exact_duplicate_within_source"] += 1
            continue
        unique.add(key)
        if group not in assigned:
            value = rng.random()
            assigned[group] = (
                "train"
                if group in forced_train or value < 0.70
                else "val"
                if value < 0.85
                else "test"
            )
        selected.append({**r, "group": f"group-{group}", "split": assigned[group]})
    coverage = {
        split: set().union(*(classes(r["rows"]) for r in selected if r["split"] == split))
        for split in ("train", "val", "test")
    }
    if coverage["train"] != {1, 2, 3, 4} or not coverage["val"] or not coverage["test"]:
        raise ValueError(f"insufficient class coverage after deduplication: {coverage}")
    destination.mkdir(parents=True)
    for r in selected:
        original = ROOT / r["image"]
        name = r["id"] + original.suffix
        image = destination / "images" / r["split"] / name
        label = destination / "labels" / r["split"] / Path(name).with_suffix(".txt")
        image.parent.mkdir(parents=True, exist_ok=True)
        label.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, image)
        label.write_text("\n".join(r["rows"]) + "\n")
    config = {
        "path": destination.as_posix(),
        "train": "images/train",
        "val": "images/val",
        "test": "images/test",
        "names": NAMES,
    }
    (destination / "data.yaml").write_text(yaml.safe_dump(config, allow_unicode=True))
    summary = {}
    for split in ("train", "val", "test"):
        part = [r for r in selected if r["split"] == split]
        summary[split] = {
            "images": len(part),
            "sources": dict(Counter(r["source"] for r in part)),
            "objects": dict(sum((classes(r["rows"]) for r in part), Counter())),
        }
    report = {
        "exports": exports,
        "seed": 42,
        "summary": summary,
        "excluded": dict(excluded),
        "records": selected,
        "limitations": [
            "Whole-frame pHash radius 4 and filename grouping do not prove scene independence",
            "Source annotations are partial; source-aware loss and evaluation are required",
            "Class 3 is general face protection; old respirator AP is not a general-mask baseline",
            "Helmet source boxes may include the entire helmeted head",
            "No manual SH17/pics images or old training replay are added",
        ],
    }
    write_json(manifest_path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "ml/data/finetune-v2")
    args = parser.parse_args()
    out = args.out.resolve()
    if not out.is_relative_to(ROOT):
        parser.error("output must stay inside repository")
    out.mkdir(parents=True, exist_ok=True)
    cv2.setNumThreads(1)
    report = prepare(out)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
