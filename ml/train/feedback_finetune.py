"""Демо-дообучение детектора СИЗ на решениях оператора.

Выгрузка `eval.feedback` хранит рамку человека с индексом типа нарушения — так
детектор учить нельзя. Здесь кадры переразмечаются детекциями рабочих весов:
  подтверждённый инцидент -> детекции как есть;
  ложный -> те же детекции без класса-виновника внутри рамки человека
  (no_helmet -> head, smoking -> cigarette); остальные типы пропускаются.
Дальше: смесь с выборкой исходного train (по умолчанию в 10 раз больше фидбэка),
дообучение от рабочих весов в отдельный прогон ml/runs/feedback-*, валидация на
неизменном val, печать «было -> стало» по классам. Рабочие веса не меняются.

Из папки ml/:
py -m train.feedback_finetune --weights runs/release-ppe/best.pt --epochs 3
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import time
from collections.abc import Callable
from pathlib import Path

ML = Path(__file__).resolve().parents[1]
DATA_DIR = ML / "data" / "ppe"
FEEDBACK_DIR = ML / "data" / "feedback"
RUNS = ML / "runs"
NAMES = ["person", "helmet", "head", "respirator", "cigarette"]
# тип нарушения -> класс детектора, который его "виноват"
CULPRIT = {"no_helmet": 2, "smoking": 4}
IMG_EXT = {".jpg", ".jpeg", ".png"}

# детекция: (класс, x1, y1, x2, y2), координаты 0..1
Det = tuple[int, float, float, float, float]


def inside(det: Det, bbox: list[float]) -> bool:
    """Центр детекции внутри рамки человека."""
    cx, cy = (det[1] + det[3]) / 2, (det[2] + det[4]) / 2
    return bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]


def relabel(entry: dict, dets: list[Det]) -> list[Det] | None:
    """Разметка детектора для записи manifest; None — тип без класса, пропуск."""
    culprit = CULPRIT.get(entry["type"])
    if culprit is None:
        return None
    if entry["verdict"] == "false":
        return [d for d in dets if not (d[0] == culprit and inside(d, entry["bbox"]))]
    return list(dets)


def det_line(d: Det) -> str:
    cls, x1, y1, x2, y2 = d
    return f"{cls} {(x1 + x2) / 2:.6f} {(y1 + y2) / 2:.6f} {x2 - x1:.6f} {y2 - y1:.6f}"


def build_feedback_set(
    manifest: list[dict],
    src: Path,
    out: Path,
    detect: Callable[[Path], list[Det]],
) -> tuple[list[Path], dict[str, int]]:
    """Пишет out/images + out/labels в классах детектора. Возвращает кадры и отчёт."""
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    images: list[Path] = []
    report: dict[str, int] = {}
    for entry in manifest:
        image = src / "images" / f"{entry['id']}.jpg"
        if not image.is_file():
            report["нет кадра"] = report.get("нет кадра", 0) + 1
            continue
        dets = detect(image)
        labels = relabel(entry, dets)
        if labels is None:
            key = f"пропущен тип {entry['type']} (нет класса детектора)"
            report[key] = report.get(key, 0) + 1
            continue
        dst = out / "images" / image.name
        shutil.copyfile(image, dst)
        (out / "labels" / f"{entry['id']}.txt").write_text(
            "".join(det_line(d) + "\n" for d in labels), encoding="utf-8"
        )
        images.append(dst)
        key = f"взят {entry['verdict']}"
        report[key] = report.get(key, 0) + 1
    return images, report


def sample_train(data_dir: Path, count: int, seed: int = 0) -> list[Path]:
    train = data_dir / "images" / "train"
    files = (
        sorted(p for p in train.iterdir() if p.suffix.lower() in IMG_EXT) if train.is_dir() else []
    )
    random.Random(seed).shuffle(files)
    return files[:count]


def write_data_yaml(path: Path, train_list: Path, val_dir: Path) -> Path:
    import yaml

    data = {
        "path": path.parent.as_posix(),
        "train": train_list.as_posix(),
        "val": val_dir.as_posix(),
        "names": dict(enumerate(NAMES)),
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def format_compare(before: dict[str, float], after: dict[str, float]) -> str:
    rows = ["класс | было mAP50 | стало mAP50 | разница"]
    for name in sorted(set(before) | set(after)):
        b, a = before.get(name), after.get(name)
        diff = f"{a - b:+.4f}" if a is not None and b is not None else "-"
        was = "-" if b is None else f"{b:.4f}"
        now = "-" if a is None else f"{a:.4f}"
        rows.append(f"{name} | {was} | {now} | {diff}")
    return "\n".join(rows)


def yolo_detect(model, conf: float) -> Callable[[Path], list[Det]]:
    def detect(image: Path) -> list[Det]:
        result = model.predict(str(image), conf=conf, verbose=False)[0]
        return [
            (int(c), *map(float, xyxy))
            for c, xyxy in zip(result.boxes.cls.tolist(), result.boxes.xyxyn.tolist(), strict=True)
        ]

    return detect


def yolo_val(model, data_yaml: Path) -> dict[str, float]:
    metrics = model.val(data=str(data_yaml), verbose=False)
    return {
        metrics.names[int(c)]: float(ap)
        for c, ap in zip(metrics.box.ap_class_index, metrics.box.ap50, strict=True)
    }


def yolo_train(weights: Path, data_yaml: Path, epochs: int, name: str, device) -> Path:
    from ultralytics import YOLO

    model = YOLO(str(weights))
    model.train(data=str(data_yaml), epochs=epochs, project=str(RUNS), name=name, device=device)
    return Path(model.trainer.save_dir) / "weights" / "best.pt"


def run(
    args: argparse.Namespace,
    detect: Callable[[Path], list[Det]],
    validate: Callable[[Path, Path], dict[str, float]],
    train: Callable[[Path, Path, int, str, object], Path],
) -> dict:
    manifest = json.loads((args.feedback / "manifest.json").read_text(encoding="utf-8"))
    name = args.name or time.strftime("feedback-%Y%m%d-%H%M%S")
    work = args.out / name
    images, report = build_feedback_set(manifest, args.feedback, work / "feedback", detect)
    for key, n in report.items():
        print(f"  {key}: {n}")
    if not images:
        raise SystemExit("Нет кадров для дообучения")
    originals = sample_train(args.data, args.ratio * len(images))
    lines = [p.resolve().as_posix() for p in [*images, *originals]]
    train_list = work / "train.txt"
    train_list.write_text("\n".join(lines) + "\n", encoding="utf-8")
    data_yaml = write_data_yaml(work / "data.yaml", train_list, args.data / "images" / "val")
    print(f"обучающая выборка: фидбэк {len(images)} + исходный train {len(originals)}")

    before = validate(args.weights, data_yaml)
    new_weights = train(args.weights, data_yaml, args.epochs, name, args.device)
    after = validate(new_weights, data_yaml)
    print(format_compare(before, after))
    result = {"run": name, "weights": str(new_weights), "report": report,
              "before": before, "after": after}  # fmt: skip
    work.mkdir(parents=True, exist_ok=True)
    (work / "compare.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--weights", type=Path, required=True, help="рабочие веса (не меняются)")
    p.add_argument("--feedback", type=Path, default=FEEDBACK_DIR)
    p.add_argument("--data", type=Path, default=DATA_DIR, help="исходный датасет с train и val")
    p.add_argument("--out", type=Path, default=RUNS, help="куда класть прогон feedback-*")
    p.add_argument("--name", default=None)
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--ratio", type=int, default=10, help="исходных кадров на один кадр фидбэка")
    p.add_argument("--conf", type=float, default=0.25, help="порог детекций для разметки")
    p.add_argument("--device", default=None)
    args = p.parse_args(argv)
    if args.name and not args.name.startswith("feedback-"):
        p.error("--name должен начинаться с feedback-")
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    from ultralytics import YOLO

    labeler = yolo_detect(YOLO(str(args.weights)), args.conf)

    def validate(weights: Path, data_yaml: Path) -> dict[str, float]:
        return yolo_val(YOLO(str(weights)), data_yaml)

    run(args, labeler, validate, yolo_train)


if __name__ == "__main__":
    main()
