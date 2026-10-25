"""Готовит PT/ONNX пакет и сравнивает качество на полном неизменном val (S28).

Из корня: PYTHONPATH=ml ml/.venv/bin/python -m train.release --pt-device 0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from export.bench import export_onnx
from train.train import RUNS, resolved_data_yaml, save_metrics
from worker.infer import load_model, make_session


def compare_metrics(pt, onnx, tolerance=0.02):
    """Проверяет обе общие метрики и каждый оценённый класс, не только среднее."""
    if set(pt["classes"]) != set(onnx["classes"]):
        raise ValueError("PT/ONNX оценили разные классы")
    deltas = {}
    for name in ["all", *pt["classes"]]:
        left, right = (pt, onnx) if name == "all" else (pt["classes"][name], onnx["classes"][name])
        deltas[name] = {key: abs(left[key] - right[key]) for key in ("mAP50", "mAP50-95")}
    if any(value > tolerance for metrics in deltas.values() for value in metrics.values()):
        raise ValueError(f"Разница PT/ONNX превышает {tolerance}: {deltas}")
    return deltas


def write_readme(out):
    """Инструкция перемещается вместе с моделями; данных и секретов в пакете нет."""
    (out / "README.md").write_text(
        "# СИЗ: YOLO11n PT + ONNX FP32\n\n"
        "Полное обучение: 50 эпох, imgsz 640. Выбран best.pt, исходная эпоха 42.\n"
        "Классы: 1 helmet, 2 head, 3 respirator, 4 cigarette; person отсутствует, "
        "воркер использует отдельный детектор людей.\n\n"
        "После распаковки из корня установленного mosprom:\n\n"
        "```bash\npython3 tasks.py run --video <ролик-или-RTSP> "
        "--ppe-weights <путь>/release-ppe/best.pt\n```\n\n"
        "Для ONNX замените best.pt на best.onnx (нужен onnxruntime).\n"
        "metrics.json — финальная валидация S11; comparison.json — PT/ONNX на "
        "всех 4509 val при одинаковом квадратном кадре, расхождение <= 0.02.\n"
        "SHA256 файлов записаны в manifest.json; проверяйте после скачивания.\n"
        "Проверка на независимых производственных роликах ещё не выполнена. "
        "Наличие респиратора не гарантирует правильное ношение.\n",
        encoding="utf-8",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=RUNS / "ppe-full-n-640")
    parser.add_argument("--out", type=Path, default=RUNS / "release-ppe")
    parser.add_argument("--pt-device", default="cpu")
    parser.add_argument("--imgsz", type=int, default=640)
    args = parser.parse_args(argv)
    import numpy as np
    import torch
    import yaml

    torch.set_num_threads(4)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "manifest.json").unlink(missing_ok=True)
    checkpoint = args.source / "weights" / "best.pt"
    target = args.out / "best.pt"
    if target.resolve() == checkpoint.resolve():
        parser.error("--out не должен совпадать с исходной папкой weights")
    shutil.copy2(checkpoint, target)
    for name in ("metrics.json", "args.yaml", "results.csv"):
        shutil.copy2(args.source / name, args.out / name)
    print("Экспорт ONNX через export.bench.export_onnx", flush=True)
    onnx_path = Path(export_onnx(str(target), imgsz=args.imgsz))
    session = make_session(str(onnx_path), "cpu")
    sample = np.zeros((1, 3, args.imgsz, args.imgsz), dtype=np.float32)
    outputs = session.run(None, {session.get_inputs()[0].name: sample})
    if not outputs or not all(np.isfinite(output).all() for output in outputs):
        raise ValueError("ONNX smoke-инференс вернул некорректные значения")
    del session
    data = resolved_data_yaml()
    results = {}
    for label, model_path, device, batch in (
        ("pt", target, args.pt_device, 16),
        ("onnx", onnx_path, "cpu", 1),
    ):
        print(f"Полный val: {label}, device={device}, batch={batch}", flush=True)
        metrics = load_model(str(model_path)).val(
            data=str(data),
            split="val",
            imgsz=args.imgsz,
            device=device,
            batch=batch,
            workers=0,
            rect=False,
            half=False,
            plots=False,
            conf=0.001,
            iou=0.7,
            max_det=300,
            augment=False,
            project=str(args.out / "validation"),
            name=label,
        )
        path = save_metrics(metrics, args.out / "validation" / label)
        results[label] = json.loads(path.read_text())
    report = {
        "split": "val",
        "imgsz": args.imgsz,
        "settings": {"rect": False, "half": False, "conf": 0.001, "iou": 0.7, "max_det": 300},
        "metrics": results,
        "absolute_deltas": compare_metrics(results["pt"], results["onnx"]),
    }
    (args.out / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
    names = yaml.safe_load(data.read_text())["names"]
    shutil.copy2(Path(__file__).with_name("data.yaml"), args.out / "data.yaml")
    write_readme(args.out)
    files = [
        "best.pt",
        "best.onnx",
        "metrics.json",
        "args.yaml",
        "results.csv",
        "comparison.json",
        "data.yaml",
        "README.md",
    ]
    manifest = {
        "source_checkpoint": str(checkpoint.resolve()),
        "source_run": args.source.name,
        "classes": names,
        "parameters": yaml.safe_load((args.out / "args.yaml").read_text()),
        "files": {
            name: hashlib.sha256((args.out / name).read_bytes()).hexdigest() for name in files
        },
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Пакет проверен: {args.out}; PT/ONNX расхождение <= 0.02", flush=True)


if __name__ == "__main__":
    main()
