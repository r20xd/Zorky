"""Явный ручной запуск дообучения ppe-v2 на подготовленных новых данных.

--check проверяет данные, веса и GPU без обучения. Обычный запуск выполняет
только обучение и сохраняет best/last, результаты эпох и JSON с метриками val.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import yaml

from .finetune_dataset import classes

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "ml/runs/release-ppe-s/best.pt"
BASE_SHA = "d151b99336e3765aa2b3c388fe8998609051287054266f50e199bbcd715276c8"
DATA = ROOT / "ml/data/finetune-v2/dataset/data.yaml"
NAMES = {0: "person", 1: "helmet", 2: "head", 3: "face_protection", 4: "cigarette"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_dataset(path):
    config = yaml.safe_load(path.read_text())
    root = Path(config["path"])
    if not root.resolve().is_relative_to(ROOT) or config["names"] != NAMES:
        raise ValueError("неверный каталог или классы подготовленного набора")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    groups, hashes = {}, {}
    expected_images, expected_labels = set(), set()
    for r in manifest["records"]:
        split = r["split"]
        if split not in ("train", "val", "test"):
            raise ValueError("неверный split")
        for registry, key in ((groups, r["group"]), (hashes, r["sha256"])):
            if key in registry and registry[key] != split:
                raise ValueError("утечка группы или изображения между split")
            registry[key] = split
        if r["source"] not in ("helmet", "cigarette", "fangchen"):
            raise ValueError("посторонний источник")
        name = r["id"] + Path(r["image"]).suffix
        image = root / "images" / split / name
        label = root / "labels" / split / Path(name).with_suffix(".txt")
        if sha(image) != r["sha256"]:
            raise ValueError(f"изменилось изображение {name}")
        rows = label.read_text().splitlines()
        if rows != r["rows"]:
            raise ValueError(f"изменились метки {name}")
        observed = set(classes(rows))
        allowed = {"helmet": {1, 2}, "cigarette": {4}, "fangchen": {3}}[r["source"]]
        if not observed <= allowed:
            raise ValueError(f"классы не соответствуют источнику {name}")
        expected_images.add(image)
        expected_labels.add(label)
    actual_images = {p for p in (root / "images").rglob("*") if p.is_file()}
    actual_labels = {p for p in (root / "labels").rglob("*.txt") if p.is_file()}
    if actual_images != expected_images or actual_labels != expected_labels:
        raise ValueError("лишние или отсутствующие пары изображение/разметка")
    return {"summary": manifest["summary"], "manifest_sha256": sha(manifest_path)}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--name", default="ppe-v3")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.epochs < 1 or args.batch < 1 or not re.fullmatch(r"[A-Za-z0-9_-]+", args.name):
        parser.error("epochs/batch должны быть положительными; name — буквы, цифры, _ или -")
    if not args.data.resolve().is_relative_to(ROOT):
        parser.error("data.yaml должен находиться в репозитории")
    return args


def main(argv=None):
    args = parse_args(argv)
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "Vladimir":
        raise SystemExit("обучение разрешено только в Vladimir")
    if sha(BASE) != BASE_SHA:
        raise SystemExit("SHA256 исходных весов отличается от ppe-v2")
    checked = validate_dataset(args.data)
    runtime = ROOT / "ml/runs/platform-training-settings"
    os.environ["YOLO_CONFIG_DIR"] = str(runtime / "ultralytics")
    os.environ["MPLCONFIGDIR"] = str(runtime / "matplotlib")
    os.environ["TORCH_HOME"] = str(runtime / "torch")
    os.environ["WANDB_MODE"] = "disabled"
    os.environ["COMET_MODE"] = "DISABLED"

    import torch
    import ultralytics
    from ultralytics import YOLO, settings

    from .source_training import SourceTrainer, source_loss_enabled
    from .train import save_metrics

    settings.update(
        {
            key: False
            for key in (
                "sync",
                "wandb",
                "mlflow",
                "comet",
                "clearml",
                "dvc",
                "neptune",
                "tensorboard",
                "hub",
            )
            if key in settings
        }
    )
    if not torch.cuda.is_available():
        raise SystemExit("CUDA недоступна; запускать из обычного терминала с доступом к RTX 5070")
    model = YOLO(str(BASE))
    if args.check:
        model.model.to("cuda").eval()
        with torch.inference_mode():
            result = model.model(torch.zeros(1, 3, 640, 640, device="cuda"))
        output = result[0] if isinstance(result, tuple) else result
        if not torch.isfinite(output).all():
            raise RuntimeError("нечисловой результат GPU-инференса")
        print(
            json.dumps(
                {
                    **checked,
                    "gpu": torch.cuda.get_device_name(0),
                    "base_sha256": BASE_SHA,
                    "output_shape": list(output.shape),
                    "training_started": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    output_directory = ROOT / "ml/runs" / args.name
    if output_directory.exists():
        raise SystemExit(f"каталог {output_directory} уже существует; выберите другое --name")
    with source_loss_enabled():
        metrics = model.train(
            trainer=SourceTrainer,
            data=str(args.data.resolve()),
            epochs=args.epochs,
            imgsz=640,
            batch=args.batch,
            device=0,
            workers=0,
            optimizer="AdamW",
            lr0=0.0003,
            lrf=0.1,
            warmup_epochs=2,
            patience=7,
            seed=42,
            deterministic=True,
            amp=False,
            mosaic=0,
            mixup=0,
            cutmix=0,
            copy_paste=0,
            close_mosaic=0,
            plots=False,
            save_period=1,
            project=str(ROOT / "ml/runs"),
            name=args.name,
            exist_ok=False,
        )
    save_dir = Path(model.trainer.save_dir)
    save_metrics(metrics, save_dir)
    (save_dir / "provenance.json").write_text(
        json.dumps(
            {
                **checked,
                "base_sha256": BASE_SHA,
                "torch": torch.__version__,
                "ultralytics": ultralytics.__version__,
                "classes": NAMES,
                "protocol": (
                    "source-aware classification loss and validation; no mixed-image augmentations"
                ),
                "test_used_during_training": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    if sha(BASE) != BASE_SHA:
        raise RuntimeError("исходные веса изменились")
    print(f"Готово: {save_dir}/weights/best.pt; метрики: {save_dir}/metrics.json")


if __name__ == "__main__":
    main()
