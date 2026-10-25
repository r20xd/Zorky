"""Воспроизводимая сборка отдельного кандидата: новые сцены + старый train.

Из корня: PYTHONPATH=ml python -m train.finetune_dataset --help
Исходные данные, разметку и рабочие веса не изменяет.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from collections import Counter
from pathlib import Path

import yaml

from .stats import valid_class

NAMES = {0: "person", 1: "helmet", 2: "head", 3: "respirator", 4: "cigarette"}
ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_path(root: Path, name: str) -> Path:
    path = root / name
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"путь за пределами набора или symlink: {name}")
    return path


def classes(rows: list[str]) -> Counter:
    result = Counter()
    for row in rows:
        class_id = valid_class(row, {1, 2, 3, 4})
        if class_id is None:
            raise ValueError(f"неверный bbox или класс: {row}")
        result[class_id] += 1
    return result


def validate_plan(records: list[dict]) -> None:
    groups, hashes, ids = {}, {}, set()
    coverage = {split: set() for split in ("train", "val", "test")}
    for r in records:
        split = r["split"]
        if split not in coverage or r["id"] in ids:
            raise ValueError("неверный split или повтор id")
        ids.add(r["id"])
        for registry, key in ((groups, r["group"]), (hashes, r["sha256"])):
            if key in registry and registry[key] != split:
                raise ValueError("утечка группы или SHA256 между split")
            registry[key] = split
        coverage[split].update(classes(r["yolo_rows"]))
    if any(value != {1, 2, 3, 4} for value in coverage.values()):
        raise ValueError("каждый новый split должен содержать все четыре целевых класса")


def replay_records(existing: Path, index: list[dict], seed: int, limit: int) -> list[dict]:
    """Стратификация только из старого train; старый val не используется."""
    pool = []
    for item in index:
        image = Path(item["image"])
        if image.parts[:2] != ("images", "train"):
            continue
        label = Path("labels") / image.relative_to("images").with_suffix(".txt")
        label_path = local_path(existing, label.as_posix())
        rows = [line for line in label_path.read_text().splitlines() if line.strip()]
        pool.append(
            {
                "id": "replay-" + image.stem,
                "image": image.as_posix(),
                "sha256": item["sha256"],
                "label": label.as_posix(),
                "label_sha256": digest(label_path),
                "yolo_rows": rows,
                "classes": sorted(classes(rows)),
                "split": "train",
                "source": "old_train_replay",
            }
        )
    random.Random(seed).shuffle(pool)
    chosen, seen = [], set()
    for cls, quota in ((4, 446), (3, 600), (2, 400), (1, 600), (None, 80), ("any", limit)):
        selected = 0
        for r in pool:
            if len(chosen) >= limit or selected >= quota:
                break
            if r["image"] in seen:
                continue
            if cls == "any" or (cls is None and not r["classes"]) or cls in r["classes"]:
                chosen.append(r)
                seen.add(r["image"])
                selected += 1
    if len(chosen) != limit:
        raise ValueError("недостаточно старых train-изображений")
    return chosen


def build(plan_path: Path, index_path: Path, existing: Path, out: Path, root=ROOT) -> dict:
    plan = json.loads(plan_path.read_text())
    if digest(index_path) != plan["existing_index_sha256"]:
        raise ValueError("изменился индекс старого набора")
    index = json.loads(index_path.read_text())["records"]
    if any("error" in r for r in index):
        raise ValueError("ошибки декодирования в старом наборе")
    records = plan["records"]
    validate_plan(records)
    replay = replay_records(existing, index, plan["seed"], plan["replay_images"])
    if len({r["id"] for r in records + replay}) != len(records + replay):
        raise ValueError("повтор id в новом наборе и replay")
    # Проверить всё до создания выходного каталога.
    for r in records + replay:
        base = existing if r.get("source") == "old_train_replay" else root
        image = local_path(base, r["image"])
        if digest(image) != r["sha256"]:
            raise ValueError(f"изменилось изображение: {r['image']}")
        if "label" in r and digest(local_path(base, r["label"])) != r["label_sha256"]:
            raise ValueError(f"изменились метки: {r['label']}")
    old_hashes = {r["sha256"] for r in index}
    if any(r["sha256"] in old_hashes for r in records):
        raise ValueError("точное пересечение новых сцен со старым набором")
    out.mkdir(parents=True, exist_ok=False)
    exported = []
    for r in records + replay:
        base = existing if r.get("source") == "old_train_replay" else root
        source = local_path(base, r["image"])
        name = r["id"] + source.suffix.lower()
        image = out / "images" / r["split"] / name
        label = out / "labels" / r["split"] / Path(name).with_suffix(".txt")
        image.parent.mkdir(parents=True, exist_ok=True)
        label.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, image)
        label.write_text("\n".join(r["yolo_rows"]) + ("\n" if r["yolo_rows"] else ""))
        exported.append(
            {
                **r,
                "export_image": image.relative_to(out).as_posix(),
                "export_label": label.relative_to(out).as_posix(),
            }
        )
    config = {
        "path": out.resolve().as_posix(),
        "train": "images/train",
        "val": "images/val",
        "test": "images/test",
        "names": NAMES,
    }
    (out / "data.yaml").write_text(yaml.safe_dump(config, allow_unicode=True))
    # Отдельный новый train нужен для отчёта, а не для обучения без replay.
    summary = {}
    for split in ("train", "val", "test"):
        subset = [r for r in exported if r["split"] == split]
        summary[split] = {
            "images": len(subset),
            "new_images": sum(r.get("source") != "old_train_replay" for r in subset),
            "objects": dict(sum((classes(r["yolo_rows"]) for r in subset), Counter())),
        }
    report = {
        "plan_sha256": digest(plan_path),
        "existing_index_sha256": digest(index_path),
        "summary": summary,
        "records": exported,
        "limitation": "Tiny exploratory holdout, agent annotations; not production CCTV validation",
    }
    (out / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--existing", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = build(args.plan, args.index, args.existing, args.out)
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
