"""INT8-квантование ONNX (onnxruntime, статическая калибровка по картинкам).

Из папки ml/ (нужен ml/requirements.txt):
    python -m export.quantize best.onnx data/ppe/images/val --out best.int8.onnx
Дальше замер: `python -m export.bench run best.int8.onnx`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


def list_images(folder: str, limit: int = 100) -> list[Path]:
    """Первые `limit` картинок папки (рекурсивно, по имени)."""
    found = sorted(p for p in Path(folder).rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)
    return found[:limit]


def preprocess(image, imgsz: int):
    """BGR HxWx3 uint8 -> float32 1x3xSxS в 0..1 (простой resize без letterbox)."""
    import cv2
    import numpy as np

    resized = cv2.resize(image, (imgsz, imgsz))
    rgb = resized[:, :, ::-1].astype(np.float32) / 255.0
    return np.ascontiguousarray(rgb.transpose(2, 0, 1)[None])


def default_out(onnx_path: str) -> str:
    p = Path(onnx_path)
    return str(p.with_name(f"{p.stem}.int8{p.suffix}"))


def quantize(
    onnx_path: str,
    images: list[Path],
    out_path: str | None = None,
    imgsz: int = 640,
    load_image=None,
) -> str:
    """FP32 ONNX -> INT8 (QDQ) с калибровкой на `images`; возвращает путь результата.

    `load_image(path) -> BGR ndarray` — подмена в тестах (по умолчанию cv2.imread).
    """
    if not images:
        raise ValueError("нет картинок для калибровки")
    from onnxruntime.quantization import CalibrationDataReader, QuantFormat, quantize_static
    from onnxruntime.quantization.shape_inference import quant_pre_process

    if load_image is None:
        import cv2

        load_image = lambda p: cv2.imread(str(p))  # noqa: E731

    import onnxruntime as ort

    input_name = (
        ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"]).get_inputs()[0].name
    )

    class Reader(CalibrationDataReader):
        def __init__(self):
            self.it = iter(images)

        def get_next(self):
            for path in self.it:
                img = load_image(path)
                if img is not None:
                    return {input_name: preprocess(img, imgsz)}
            return None

    out = out_path or default_out(onnx_path)
    prepared = str(Path(out).with_name(Path(out).stem + ".pre.onnx"))
    try:
        quant_pre_process(onnx_path, prepared)
        source = prepared
    except Exception:  # noqa: BLE001 - предобработка необязательна
        source = onnx_path
    try:
        quantize_static(source, out, Reader(), quant_format=QuantFormat.QDQ)
    finally:
        Path(prepared).unlink(missing_ok=True)
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("onnx")
    parser.add_argument("images", help="папка с картинками для калибровки (ml/data/ppe/...)")
    parser.add_argument("--out")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    imgs = list_images(args.images, args.limit)
    print(quantize(args.onnx, imgs, args.out, args.imgsz))


if __name__ == "__main__":
    main()
