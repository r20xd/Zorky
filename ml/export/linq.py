"""ONNX под LinQ: три модели со статическим входом 1x3x640x640 и без NMS внутри.

Из папки ml/ (нужен ml/requirements.txt):
    python -m export.linq export --ppe runs/ppe-full-n-640/weights/best.pt \
        --people yolo11n.pt --pose yolo11n-pose.pt --out linq_models
    python -m export.linq check linq_models/ppe.onnx
    python -m export.linq calib data/ppe/images/val --out linq_calib   # 200 картинок
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from .quantize import list_images

VIDEO_SUFFIXES = {".mp4", ".avi", ".mkv", ".mov"}

DEFAULT_OPSET = 12  # осторожный выбор до уточнения SDK LinQ (см. README.md, раздел «LinQ»)
CALIB_COUNT = 200
MODEL_NAMES = ("ppe", "people", "pose")


def check_onnx(onnx_path: str, imgsz: int = 640) -> list[str]:
    """Проверяет ONNX под LinQ; возвращает список проблем (пусто — всё хорошо)."""
    import onnx

    model = onnx.load(onnx_path)
    problems = []
    inputs = [
        i for i in model.graph.input if i.name not in {t.name for t in model.graph.initializer}
    ]
    if len(inputs) != 1:
        problems.append(f"входов {len(inputs)}, нужен 1")
    for inp in inputs[:1]:
        dims = [
            d.dim_value if d.HasField("dim_value") else None for d in inp.type.tensor_type.shape.dim
        ]
        if dims != [1, 3, imgsz, imgsz]:
            problems.append(f"вход {dims}, нужен статический [1, 3, {imgsz}, {imgsz}]")
    for out in model.graph.output:
        if any(not d.HasField("dim_value") for d in out.type.tensor_type.shape.dim):
            problems.append(f"выход {out.name} с динамической формой")
    if any(n.op_type == "NonMaxSuppression" for n in model.graph.node):
        problems.append("внутри есть NonMaxSuppression")
    return problems


def export_model(weights: str, out_path: str, imgsz: int = 640, opset: int = DEFAULT_OPSET) -> str:
    """YOLO .pt -> ONNX (batch 1, статический, без NMS), копия в `out_path`."""
    from worker.infer import load_model

    produced = load_model(weights).export(
        format="onnx", imgsz=imgsz, opset=opset, dynamic=False, batch=1, nms=False, simplify=True
    )
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    if Path(produced).resolve() != Path(out_path).resolve():
        shutil.copyfile(produced, out_path)
    problems = check_onnx(out_path, imgsz)
    if problems:
        raise RuntimeError(f"{out_path}: " + "; ".join(problems))
    return out_path


def sample_video_frames(video: Path, count: int, imgsz: int = 640):
    """До `count` кадров, равномерно по ролику, как BGR imgsz x imgsz."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, total // max(1, count))
    frames = []
    for index in range(0, total, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = cap.read()
        if ok:
            frames.append(cv2.resize(frame, (imgsz, imgsz)))
        if len(frames) >= count:
            break
    cap.release()
    return frames


def collect_calibration(
    src: str, out_dir: str, count: int = CALIB_COUNT, imgsz: int = 640, load_image=None
) -> list[Path]:
    """До `count` кадров из `src` (папка картинок или роликов) в `out_dir`: JPEG imgsz x imgsz.

    Ролики дают кадры равномерно; настоящие кадры камер калибруют лучше случайных."""
    import cv2

    from_videos = load_image is None  # ролики читаем сами, свой загрузчик — только для картинок
    load_image = load_image or (lambda p: cv2.imread(str(p)))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    videos = sorted(p for p in Path(src).rglob("*") if p.suffix.lower() in VIDEO_SUFFIXES)
    if videos and from_videos:
        per_video = max(1, count // len(videos))
        for video in videos:
            for frame in sample_video_frames(video, per_video, imgsz):
                if len(saved) >= count:
                    return saved
                target = out / f"{len(saved):04d}.jpg"
                cv2.imwrite(str(target), frame)
                saved.append(target)
        return saved
    for path in list_images(src, limit=10**9):
        if len(saved) >= count:
            break
        img = load_image(path)
        if img is None:
            continue
        target = out / f"{len(saved):04d}.jpg"
        cv2.imwrite(str(target), cv2.resize(img, (imgsz, imgsz)))
        saved.append(target)
    return saved


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    exp = sub.add_parser("export", help="три модели .pt -> ONNX")
    exp.add_argument("--ppe", default=None, help="веса СИЗ; не заданы — модель СИЗ не собираем")
    exp.add_argument("--people", default="yolo11n.pt", help="люди и техника")
    exp.add_argument("--pose", default="yolo11n-pose.pt", help="поза")
    exp.add_argument("--out", default="linq_models")
    exp.add_argument("--imgsz", type=int, default=640)
    exp.add_argument("--opset", type=int, default=DEFAULT_OPSET)
    chk = sub.add_parser("check", help="проверить ONNX")
    chk.add_argument("onnx")
    chk.add_argument("--imgsz", type=int, default=640)
    cal = sub.add_parser("calib", help="собрать калибровочные картинки")
    cal.add_argument("images")
    cal.add_argument("--out", default="linq_calib")
    cal.add_argument("--count", type=int, default=CALIB_COUNT)
    cal.add_argument("--imgsz", type=int, default=640)
    args = parser.parse_args(argv)
    if args.cmd == "export":
        weights_by_name = dict(zip(MODEL_NAMES, (args.ppe, args.people, args.pose), strict=False))
        for name, weights in weights_by_name.items():
            if weights:
                path = str(Path(args.out) / f"{name}.onnx")
                print(export_model(weights, path, args.imgsz, args.opset))
    elif args.cmd == "check":
        problems = check_onnx(args.onnx, args.imgsz)
        print("OK" if not problems else "\n".join(problems))
        raise SystemExit(1 if problems else 0)
    else:
        print(len(collect_calibration(args.images, args.out, args.count, args.imgsz)))


if __name__ == "__main__":
    main()
