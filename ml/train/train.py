"""Обучение детектора СИЗ (ML-1).

    ml/.venv/Scripts/python ml/train/train.py --epochs 50 --base yolo11n.pt

Нужен ml/requirements.txt (ultralytics). Результаты — в ml/runs/ (вне git).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ML = Path(__file__).resolve().parents[1]
DATA_YAML = Path(__file__).with_name("data.yaml")
DATA_DIR = ML / "data" / "ppe"
RUNS = ML / "runs"


def check_hardware(torch, device: str | None) -> None:
    """Показывает окружение и заранее отклоняет недоступную CUDA."""
    available = torch.cuda.is_available()
    print(f"torch: {torch.__version__}; CUDA: {torch.version.cuda or 'нет'}")
    if available:
        for index in range(torch.cuda.device_count()):
            print(f"GPU {index}: {torch.cuda.get_device_name(index)}")
    else:
        print("GPU CUDA: недоступен")
    wants_cuda = device is not None and (
        device.startswith("cuda") or all(part.strip().isdigit() for part in device.split(","))
    )
    if wants_cuda and not available:
        raise SystemExit(
            f"--device {device}: CUDA недоступна. Установите torch с CUDA 12.8:\n"
            "python -m pip install --upgrade --force-reinstall torch torchvision "
            "--index-url https://download.pytorch.org/whl/cu128\n"
            "Запустите из того же окружения с доступом к GPU и драйвером NVIDIA."
        )


def print_class_metrics(metrics) -> None:
    """mAP50 только для классов, для которых валидатор собрал метрики."""
    if metrics is None or not len(metrics.box.ap_class_index):
        print("mAP50 по классам: нет результатов валидации")
        return
    print("mAP50 по классам:")
    for class_id, ap50 in zip(metrics.box.ap_class_index, metrics.box.ap50, strict=True):
        print(f"  {metrics.names[int(class_id)]}: {float(ap50):.4f}")


def save_metrics(metrics, save_dir: Path) -> Path:
    """Сохраняет только реально оценённые классы в каталоге текущего прогона."""
    classes = {}
    if metrics is not None:
        for class_id, ap50, ap in zip(
            metrics.box.ap_class_index, metrics.box.ap50, metrics.box.ap, strict=True
        ):
            name = metrics.names[int(class_id)]
            classes[name] = {"class_id": int(class_id), "mAP50": float(ap50), "mAP50-95": float(ap)}
    data = {
        "mAP50": float(metrics.box.map50) if classes else None,
        "mAP50-95": float(metrics.box.map) if classes else None,
        "classes": classes,
    }
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    path = save_dir / "metrics.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(f"Метрики: {path}")
    print("Строки для таблицы замеров:")
    print("| Прогон | Класс | mAP50 | mAP50-95 |")
    print("|---|---|---:|---:|")
    for name, values in classes.items():
        print(f"| {save_dir.name} | {name} | {values['mAP50']:.4f} | {values['mAP50-95']:.4f} |")
    if not classes:
        print("Нет результатов валидации; значения в JSON — null.")
    return path


def resolved_data_yaml() -> Path:
    """Копия data.yaml с абсолютным path — иначе ultralytics ищет данные не там."""
    import yaml

    data = yaml.safe_load(DATA_YAML.read_text(encoding="utf-8"))
    data["path"] = DATA_DIR.as_posix()
    out = RUNS / "data.resolved.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Обучение детектора СИЗ")
    parser.add_argument("--base", default="yolo11n.pt", help="стартовые веса")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--name", default="ppe")
    parser.add_argument("--device", default=None, help="cpu, 0, mps; по умолчанию сам")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--resume",
        nargs="?",
        const="",
        default=None,
        help="продолжить checkpoint; без пути — ml/runs/<name>/weights/last.pt",
    )
    parser.add_argument("--patience", type=int, default=100, help="эпох без улучшения; 0 отключает")
    args = parser.parse_args(argv)
    if args.patience < 0:
        parser.error("--patience должен быть >= 0")
    if args.resume is not None:
        args.resume = Path(args.resume) if args.resume else RUNS / args.name / "weights" / "last.pt"
        if not args.resume.is_file():
            parser.error(f"checkpoint не найден: {args.resume}")
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    import torch

    check_hardware(torch, args.device)

    from ultralytics import YOLO

    model = YOLO(str(args.resume) if args.resume is not None else args.base)
    metrics = model.train(
        data=str(resolved_data_yaml()),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        project=str(RUNS),
        name=args.name,
        device=args.device,
        workers=args.workers,
        resume=args.resume is not None,
        patience=args.patience,
    )
    print_class_metrics(metrics)
    save_metrics(metrics, model.trainer.save_dir)


if __name__ == "__main__":
    main()
