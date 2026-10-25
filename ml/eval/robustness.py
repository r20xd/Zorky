"""Устойчивость модели СИЗ к условиям съёмки.

Искажения: темнота, туман, размытие движения, шум, JPEG, контровой свет.
Val размеченного набора прогоняется через них, mAP50 и mAP50-95 по классам - против исходного.
Искажённые копии кладутся во временную папку (по умолчанию ml/runs/robustness, вне git).
Кадры demo/images/ без разметки: только число детекций на исходном и искажённом кадре.

Запуск из папки ml/:
py -m eval.robustness val [--weights runs/release-ppe/best.pt] [--device 0] [--limit N]
py -m eval.robustness demo [--weights ...]
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import cv2
import numpy as np

ML = Path(__file__).resolve().parents[1]
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp"}


def dark(img: np.ndarray) -> np.ndarray:
    """Ночь: гамма 2.5 и спад яркости."""
    x = (img.astype(np.float32) / 255.0) ** 2.5 * 0.7
    return np.clip(x * 255, 0, 255).astype(np.uint8)


def fog(img: np.ndarray) -> np.ndarray:
    """Туман/пыль: смесь со светло-серым, I*t + A*(1-t), t=0.5."""
    t = 0.5
    x = img.astype(np.float32) * t + 215.0 * (1 - t)
    return np.clip(x, 0, 255).astype(np.uint8)


def motion_blur(img: np.ndarray) -> np.ndarray:
    """Размытие движения: горизонтальное ядро 15 пикселей."""
    k = np.zeros((15, 15), np.float32)
    k[7, :] = 1.0 / 15
    return cv2.filter2D(img, -1, k)


def noise(img: np.ndarray) -> np.ndarray:
    """Гауссов шум, сигма 25 (детерминированный)."""
    rng = np.random.default_rng(0)
    x = img.astype(np.float32) + rng.normal(0, 25, img.shape)
    return np.clip(x, 0, 255).astype(np.uint8)


def jpeg20(img: np.ndarray) -> np.ndarray:
    """JPEG качества 20."""
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 20])
    assert ok
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def backlight(img: np.ndarray) -> np.ndarray:
    """Контровой свет: яркое пятно сверху по центру, предметы темнеют, контраст падает."""
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d2 = ((xx - w / 2) / (0.6 * w)) ** 2 + (yy / (0.7 * h)) ** 2
    glow = np.exp(-d2)[..., None]
    x = img.astype(np.float32) * 0.6 * (1 - 0.5 * glow) + 255.0 * glow * 0.9
    return np.clip(x, 0, 255).astype(np.uint8)


DISTORTIONS = {
    "dark": dark,
    "fog": fog,
    "motion_blur": motion_blur,
    "noise": noise,
    "jpeg20": jpeg20,
    "backlight": backlight,
}


def list_images(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in IMG_EXT)


def make_distorted_set(
    src_root: Path, dst_root: Path, name: str | None, limit: int | None = None
) -> Path:
    """Копия val с искажением name (None - без него), метки копируются. Возвращает data.yaml."""
    imgs = list_images(src_root / "images" / "val")[:limit]
    (dst_root / "images" / "val").mkdir(parents=True, exist_ok=True)
    (dst_root / "labels" / "val").mkdir(parents=True, exist_ok=True)
    fn = DISTORTIONS[name] if name else None
    for p in imgs:
        im = cv2.imread(str(p))
        if im is None:
            continue
        # PNG-вход пишем JPEG-ом высокого качества, чтобы единственным искажением было заданное
        out = dst_root / "images" / "val" / (p.stem + ".jpg")
        cv2.imwrite(
            str(out),
            fn(im) if fn else im,
            [cv2.IMWRITE_JPEG_QUALITY, 100 if fn is not jpeg20 else 95],
        )
        lab = src_root / "labels" / "val" / (p.stem + ".txt")
        if lab.exists():
            shutil.copy(lab, dst_root / "labels" / "val" / lab.name)
    names = _names(src_root)
    yaml_path = dst_root / "data.yaml"
    lines = [f"path: {dst_root.resolve()}", "train: images/val", "val: images/val", "names:"]
    lines += [f"  {i}: {n}" for i, n in names.items()]
    yaml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return yaml_path


def _names(src_root: Path) -> dict[int, str]:
    import yaml

    cfg = yaml.safe_load((ML / "train" / "data.yaml").read_text(encoding="utf-8"))
    return {int(k): v for k, v in cfg["names"].items()}


def val_metrics(model, data_yaml: Path, imgsz: int, device) -> dict:
    """mAP50 и mAP50-95 общие и по классам (только классы с метками в наборе)."""
    r = model.val(
        data=str(data_yaml), imgsz=imgsz, device=device, plots=False, verbose=False, workers=2
    )
    names = r.names
    classes = {}
    for i, c in enumerate(r.box.ap_class_index):
        classes[names[int(c)]] = {"mAP50": float(r.box.ap50[i]), "mAP50-95": float(r.box.ap[i])}
    return {"mAP50": float(r.box.map50), "mAP50-95": float(r.box.map), "classes": classes}


def run_val(
    weights: Path, src_root: Path, work: Path, imgsz=640, device="0", limit=None, only=None
) -> dict:
    from ultralytics import YOLO

    model = YOLO(str(weights))
    out = {}
    for name in ["original", *DISTORTIONS]:
        if only and name != "original" and name not in only:
            continue
        d = work / name
        y = make_distorted_set(src_root, d, None if name == "original" else name, limit)
        out[name] = val_metrics(model, y, imgsz, device)
        shutil.rmtree(d / "images", ignore_errors=True)  # копии больше не нужны, место важнее
        print(name, round(out[name]["mAP50"], 4), round(out[name]["mAP50-95"], 4), flush=True)
    return out


def drops(result: dict) -> dict:
    """Падение (абсолютное, в п.п. mAP) каждого искажения против original: общее и по классам."""
    base = result["original"]
    table = {}
    for name, m in result.items():
        if name == "original":
            continue
        row = {
            "mAP50": m["mAP50"] - base["mAP50"],
            "mAP50-95": m["mAP50-95"] - base["mAP50-95"],
            "classes": {},
        }
        for c, v in m["classes"].items():
            b = base["classes"].get(c)
            if b:
                row["classes"][c] = {k: v[k] - b[k] for k in ("mAP50", "mAP50-95")}
        table[name] = row
    return table


def worst(result: dict) -> list[tuple[str, float]]:
    """Искажения по убыванию падения mAP50-95 (отрицательная дельта первой)."""
    d = drops(result)
    return sorted(((n, r["mAP50-95"]) for n, r in d.items()), key=lambda t: t[1])


def format_table(result: dict) -> str:
    """Markdown: строки - искажения, столбцы - mAP50 / mAP50-95 общие и по классам."""
    classes = list(result["original"]["classes"])
    head = ["искажение", "mAP50", "mAP50-95"] + [
        f"{c} {k}" for c in classes for k in ("50", "50-95")
    ]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for name, m in result.items():
        cells = [name, f"{m['mAP50']:.3f}", f"{m['mAP50-95']:.3f}"]
        for c in classes:
            v = m["classes"].get(c)
            cells += [f"{v['mAP50']:.3f}", f"{v['mAP50-95']:.3f}"] if v else ["-", "-"]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def run_demo(weights: Path, images: Path, out_dir: Path, imgsz=640, device="0") -> dict:
    """Число детекций на кадрах без разметки: исходный и искажённый. Примеры - в out_dir."""
    from ultralytics import YOLO

    model = YOLO(str(weights))
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {n: 0 for n in ["original", *DISTORTIONS]}
    paths = list_images(images)
    for p in paths:
        im = cv2.imread(str(p))
        for n in counts:
            x = im if n == "original" else DISTORTIONS[n](im)
            r = model.predict(x, imgsz=imgsz, device=device, verbose=False)[0]
            counts[n] += len(r.boxes)
            if p == paths[0]:
                cv2.imwrite(str(out_dir / f"{n}.jpg"), r.plot())
    return {"frames": len(paths), "detections": counts}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["val", "demo"])
    ap.add_argument("--weights", type=Path, default=ML / "runs/release-ppe/best.pt")
    ap.add_argument("--data", type=Path, default=ML / "data/ppe")
    ap.add_argument("--images", type=Path, default=ML.parent / "demo/images")
    ap.add_argument("--work", type=Path, default=ML / "runs/robustness")
    ap.add_argument("--device", default="0")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args(argv)
    if a.cmd == "val":
        res = run_val(a.weights, a.data, a.work, a.imgsz, a.device, a.limit, a.only)
        a.work.mkdir(parents=True, exist_ok=True)
        (a.work / "result.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(format_table(res))
        print("хуже всего (дельта mAP50-95):", worst(res))
    else:
        res = run_demo(a.weights, a.images, a.work / "demo", a.imgsz, a.device)
        (a.work / "demo.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
