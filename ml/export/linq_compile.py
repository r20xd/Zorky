"""ONNX -> программа .tpu для LinQ (H1M), по compile.py и tpu_framework.pdf (SDK 2026.9.45).

Запуск только на Linux x86_64 / Python 3.12.11 (WSL2 Ubuntu или Docker python:3.12)
с колёсами из packets/ SDK (pip install -r packets/requirements.txt). Из папки ml/:
    python -m export.linq_compile ppe=linq_models/ppe.onnx people=linq_models/people.onnx \
        pose=linq_models/pose.onnx --calib ../demo/images --calib data/ppe/images/val \
        --out linq_programs
Результат: `<имя>_b1_<o1|o5>_h1m.tpu` и рядом `.tpu.json` (вход и выходы для воркера).

Конвейер как в образце: убрать инициализаторы из inputs, запретить динамические формы,
preliminary_model_conditioning(ModelProto), RegularModel(model).calibrate_torch(
calibration_data={вход: float32 N x 3 x H x W}) (один тензор, кадры по оси 0 - так в руководстве),
quantize(thresholds), save/QuantizedModel.load, Network.from_quantized_model,
compiler.compile_(hardware_parameters=TPU_H1M, network=..., parameters=O5|O1),
TpuProgram.from_executable(...).to_file.
Вызовы SDK собраны в `Sdk`. Если компилятор не принимает голову YOLO, отрежьте ONNX через
get_sub_model (раздел «Sub model» руководства); отрезанное делает постобработка (`decode_obb`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path

from .quantize import list_images

IMGSZ = 640
CALIB_MIN, CALIB_MAX = 200, 500
PLATFORM = "h1m"
PRESETS = ("O5", "O1")  # O5 — попытка, при сбое O1


class Sdk:
    """Тонкая обёртка над SDK организаторов (модуль tpu_framework); в тестах подменяется."""

    def __init__(self, module=None):
        if module is None:
            import importlib

            try:
                module = importlib.import_module("tpu_framework")
            except ImportError as exc:
                raise RuntimeError(
                    "нет tpu_framework: запускайте в Linux x86_64 / Python 3.12 "
                    "(WSL2 Ubuntu или Docker python:3.12) и поставьте колёса SDK "
                    "(pip install -r packets/requirements.txt)"
                ) from exc
        self.m = module

    @property
    def version(self) -> str:
        return str(getattr(self.m, "__version__", "unknown"))

    def condition(self, onnx_model):
        """ModelProto -> ModelProto (preliminary_model_conditioning принимает модель, не путь)."""
        return self.m.preliminary_model_conditioning(onnx_model)

    def calibrate(self, onnx_model, input_name: str, frames):
        """Возвращает (regular_model, thresholds); frames - float32 N x 3 x H x W."""
        regular = self.m.RegularModel(model=onnx_model)
        thresholds = regular.calibrate_torch(calibration_data={input_name: frames})
        return regular, thresholds

    def quantize(self, calibrated):
        regular, thresholds = calibrated
        return regular.quantize(thresholds)

    def compile(self, quant_model, preset: str, out_path: str):
        """Квантованная модель -> .tpu (save/load через временную папку, как в образце)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            quant_model.save(file_dir=tmp, file_name="quant.qm.onnx")
            loaded = self.m.QuantizedModel.load(str(Path(tmp) / "quant.qm.onnx"))
        network, _ = self.m.Network.from_quantized_model(loaded)
        executable, descriptions = self.m.compiler.compile_(
            hardware_parameters=self.m.TPU_H1M,
            network=network,
            parameters=getattr(self.m, preset),
        )
        self.m.TpuProgram.from_executable(executable, descriptions).to_file(out_path)


def calibration_images(
    sources: list[str], count: int = CALIB_MAX, seed: int = 0, minimum: int = CALIB_MIN
) -> list[Path]:
    """200-500 настоящих кадров из папок-источников (поровну, перемешано, но воспроизводимо)."""
    pools = [list_images(s, limit=10**9) for s in sources]
    pools = [p for p in pools if p]
    if not pools:
        raise FileNotFoundError(f"в {sources} нет картинок для калибровки")
    rng = random.Random(seed)
    picked: list[Path] = []
    share = max(1, count // len(pools))
    for pool in pools:
        picked += rng.sample(pool, min(share, len(pool)))
    if len(picked) < minimum:
        raise ValueError(f"кадров для калибровки {len(picked)}, нужно от {minimum}")
    return picked[:count]


def letterbox_tensor(image, size: int):
    """BGR -> 1x3xSxS float32 RGB 0..1, серые поля 114: как в воркере (worker.detect)."""
    import cv2
    import numpy as np

    height, width = image.shape[:2]
    scale = min(size / height, size / width)
    new_w, new_h = round(width * scale), round(height * scale)
    resized = image if (new_w, new_h) == (width, height) else cv2.resize(image, (new_w, new_h))
    left, top = round((size - new_w) / 2 - 0.1), round((size - new_h) / 2 - 0.1)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[top : top + new_h, left : left + new_w] = resized
    tensor = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    return np.ascontiguousarray(tensor)


def load_frames(paths: list[Path], imgsz: int = IMGSZ):
    """Кадры -> один float32 тензор N x 3 x H x W (формат calibration_data из руководства)."""
    import cv2
    import numpy as np

    arrays = []
    for p in paths:
        img = cv2.imread(str(p))
        if img is not None:
            arrays.append(letterbox_tensor(img, imgsz))
    if not arrays:
        raise ValueError("ни один кадр для калибровки не читается")
    return np.concatenate(arrays, axis=0).astype(np.float32)


def prepare_onnx(onnx_path: str):
    """Как в образце: убрать инициализаторы из inputs, запретить динамические формы."""
    import onnx

    model = onnx.load(onnx_path)
    init = {t.name for t in model.graph.initializer}
    for i in [i for i in model.graph.input if i.name in init]:
        model.graph.input.remove(i)
    for i in model.graph.input:
        shape = [d.dim_value for d in i.type.tensor_type.shape.dim]
        if 0 in shape:
            raise ValueError(
                f"{onnx_path}: вход {i.name!r} динамический {shape}, нужна фиксированная форма"
            )
    return model


def onnx_io(model) -> tuple[str, list[str]]:
    """Имя единственного входа и имена выходов (ModelProto без инициализаторов в inputs)."""
    g = model.graph
    return g.input[0].name, [o.name for o in g.output]


def onnx_names(onnx_path: str) -> dict[int, str]:
    """Имена классов из метаданных ONNX (их пишет ultralytics); пусто, если их нет."""
    import ast

    try:
        import onnx

        model = onnx.load(onnx_path, load_external_data=False)
    except (ImportError, OSError):
        return {}
    for prop in model.metadata_props:
        if prop.key == "names":
            try:
                return {int(k): str(v) for k, v in ast.literal_eval(prop.value).items()}
            except (ValueError, SyntaxError, AttributeError):
                return {}
    return {}


def program_name(name: str, batch: int, preset: str) -> str:
    return f"{name}_b{batch}_{preset.lower()}_{PLATFORM}.tpu"


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_program(
    onnx_path: str,
    out_dir: str,
    name: str,
    sdk: Sdk,
    images: list[Path],
    names: dict | None = None,
    frames=None,
) -> dict:
    """ONNX -> `<name>_b<батч>_<preset>_h1m.tpu` + `.tpu.json` в out_dir. Возвращает описание."""
    from .linq import check_onnx

    problems = check_onnx(onnx_path, IMGSZ)
    if problems:
        raise ValueError(f"{onnx_path}: " + "; ".join(problems))
    stages: dict[str, float] = {}

    def log(stage: str) -> None:
        print(f"[{name}] {stage}: {time.monotonic() - started:.0f} с от старта", flush=True)

    started = time.monotonic()
    t0 = time.monotonic()
    model = sdk.condition(prepare_onnx(onnx_path))
    stages["optimization"] = time.monotonic() - t0
    log("оптимизация готова")
    input_name, outputs = onnx_io(model)
    batch = model.graph.input[0].type.tensor_type.shape.dim[0].dim_value
    if frames is None:
        frames = load_frames(images)
    t0 = time.monotonic()
    calibrated = sdk.calibrate(model, input_name, frames)
    stages["calibration"] = time.monotonic() - t0
    log("калибровка готова")
    t0 = time.monotonic()
    quantized = sdk.quantize(calibrated)
    stages["quantization"] = time.monotonic() - t0
    log("квантование готово")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    used, out_path, errors = None, "", []
    for preset in PRESETS:
        out_path = str(Path(out_dir) / program_name(name, batch, preset))
        t0 = time.monotonic()
        try:
            sdk.compile(quantized, preset, out_path)
            stages[f"compilation_{preset}"] = time.monotonic() - t0
            log(f"компиляция {preset} готова")
            used = preset
            break
        except Exception as exc:  # noqa: BLE001 - любой сбой компилятора: пробуем следующий пресет
            stages[f"compilation_{preset}_failed"] = time.monotonic() - t0
            errors.append(f"{preset}: {exc}")
    if used is None:
        raise RuntimeError("компилятор отверг модель: " + "; ".join(errors))
    desc = {
        "file": Path(out_path).name,
        "input": input_name,
        "outputs": outputs,
        "names": {str(k): v for k, v in (names or onnx_names(onnx_path)).items()},
        "sha256": sha256(out_path),
        "sdk_version": sdk.version,
        "preset": used,
        "platform": PLATFORM,
        "batch": batch,
        "calibration_frames": int(frames.shape[0]),
        "stage_seconds": {k: round(v, 1) for k, v in stages.items()},
    }
    Path(out_path + ".json").write_text(
        json.dumps(desc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return desc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("models", nargs="+", help="имя=путь.onnx (ppe, people, pose)")
    ap.add_argument("--calib", action="append", required=True, help="папка с кадрами (повторять)")
    ap.add_argument("--count", type=int, default=CALIB_MAX)
    ap.add_argument(
        "--min-frames", type=int, default=CALIB_MIN, help="меньше кадров калибровка не пойдёт"
    )
    ap.add_argument("--out", default="linq_programs")
    args = ap.parse_args(argv)
    try:
        sdk = Sdk()
    except RuntimeError as exc:
        print(f"СТОП: {exc}", file=sys.stderr)
        return 2
    images = calibration_images(args.calib, args.count, minimum=args.min_frames)
    for spec in args.models:
        name, _, path = spec.partition("=")
        desc = build_program(path, args.out, name, sdk, images)
        print(f"{name}: {desc['file']}, preset {desc['preset']}, sha256 {desc['sha256'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
