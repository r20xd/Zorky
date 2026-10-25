"""Мелкие объекты: каска в десятки пикселей, три способа поиска СИЗ.

Val размеченного набора уменьшается так, чтобы медианная каска кадра была 16, 24 или 32 px
(кадр приводится к холсту 640x640, картинка уменьшается и ставится в центр серого холста:
камера «дальше», размер кадра тот же). Способы:
  full640 - СИЗ-детектор на кадре целиком, imgsz 640 (как на LinQ);
  full960 - то же, imgsz 960 (только замер, LinQ остаётся 640);
  crops   - детектор людей (yolo11n, как в воркере) -> вырезка человека -> СИЗ-детектор на вырезке.
Считаются mAP50 и mAP50-95 по классам СИЗ (helmet, head, respirator, cigarette) единым счётчиком
для всех способов и время на кадр (GPU, кадр за кадром, без учёта чтения файла).

Запуск из папки ml/:
py -m eval.small_objects [--weights runs/release-ppe/best.pt] [--people ../yolo11n.pt] [--limit 300]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

ML = Path(__file__).resolve().parents[1]
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp"}
CANVAS = 640
HELMET = 1
PPE_CLASSES = {1: "helmet", 2: "head", 3: "respirator", 4: "cigarette"}
GRAY = 114


def person_crop(box, w: int, h: int, margin: float = 0.1) -> tuple[int, int, int, int]:
    """Окно вырезки вокруг человека: bbox (px) + поля margin от размеров, в границах кадра."""
    x1, y1, x2, y2 = (float(v) for v in box)
    mx, my = (x2 - x1) * margin, (y2 - y1) * margin
    cx1, cy1 = max(0, int(np.floor(x1 - mx))), max(0, int(np.floor(y1 - my)))
    cx2, cy2 = min(w, int(np.ceil(x2 + mx))), min(h, int(np.ceil(y2 + my)))
    if cx2 <= cx1:
        cx2 = min(w, cx1 + 1)
        cx1 = cx2 - 1
    if cy2 <= cy1:
        cy2 = min(h, cy1 + 1)
        cy1 = cy2 - 1
    return cx1, cy1, cx2, cy2


def boxes_to_frame(boxes: np.ndarray, window) -> np.ndarray:
    """xyxy в пикселях вырезки -> xyxy в пикселях кадра (сдвиг на левый верхний угол окна)."""
    out = np.asarray(boxes, np.float32).reshape(-1, 4).copy()
    out[:, [0, 2]] += window[0]
    out[:, [1, 3]] += window[1]
    return out


def shrink_scene(img: np.ndarray, labels: np.ndarray, target: float):
    """Кадр -> холст 640x640 -> уменьшение до медианной каски `target` px (sqrt площади).

    labels: (n, 5) [cls, cx, cy, w, h] нормированные. Возвращает (холст, метки, масштаб);
    None, если в кадре нет касок. Масштаб не больше 1 (увеличивать не будем).
    """
    h, w = img.shape[:2]
    k = CANVAS / max(h, w)
    nw, nh = max(1, round(w * k)), max(1, round(h * k))
    base = np.full((CANVAS, CANVAS, 3), GRAY, np.uint8)
    ox, oy = (CANVAS - nw) // 2, (CANVAS - nh) // 2
    base[oy : oy + nh, ox : ox + nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    # метки в пикселях холста
    lab = np.asarray(labels, np.float32).reshape(-1, 5)
    px = np.empty_like(lab)
    px[:, 0] = lab[:, 0]
    px[:, 1] = ox + lab[:, 1] * nw
    px[:, 2] = oy + lab[:, 2] * nh
    px[:, 3] = lab[:, 3] * nw
    px[:, 4] = lab[:, 4] * nh
    hel = px[px[:, 0] == HELMET]
    if not len(hel):
        return None
    size = float(np.median(np.sqrt(hel[:, 3] * hel[:, 4])))
    s = min(1.0, target / max(size, 1e-6))
    sw = max(1, round(CANVAS * s))
    small = cv2.resize(base, (sw, sw), interpolation=cv2.INTER_AREA)
    canvas = np.full((CANVAS, CANVAS, 3), GRAY, np.uint8)
    o = (CANVAS - sw) // 2
    canvas[o : o + sw, o : o + sw] = small
    sc = sw / CANVAS
    out = px.copy()
    out[:, 1] = (o + px[:, 1] * sc) / CANVAS
    out[:, 2] = (o + px[:, 2] * sc) / CANVAS
    out[:, 3] = px[:, 3] * sc / CANVAS
    out[:, 4] = px[:, 4] * sc / CANVAS
    return canvas, out, s


def xywhn_to_xyxy(lab: np.ndarray, size: int = CANVAS) -> np.ndarray:
    c = lab[:, 1:5] * size
    return np.stack(
        [
            c[:, 0] - c[:, 2] / 2,
            c[:, 1] - c[:, 3] / 2,
            c[:, 0] + c[:, 2] / 2,
            c[:, 1] + c[:, 3] / 2,
        ],
        1,
    )


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = np.asarray(a, np.float32).reshape(-1, 4)
    b = np.asarray(b, np.float32).reshape(-1, 4)
    lt = np.maximum(a[:, None, :2], b[None, :, :2])
    rb = np.minimum(a[:, None, 2:], b[None, :, 2:])
    inter = np.clip(rb - lt, 0, None).prod(2)
    aa = (a[:, 2:] - a[:, :2]).prod(1)
    ab = (b[:, 2:] - b[:, :2]).prod(1)
    return inter / (aa[:, None] + ab[None, :] - inter + 1e-9)


def nms_classwise(boxes, scores, cls, thr: float = 0.6) -> list[int]:
    """Индексы выживших боксов: NMS отдельно по каждому классу."""
    keep = []
    for c in np.unique(cls):
        idx = np.where(cls == c)[0]
        idx = idx[np.argsort(-scores[idx])]
        while len(idx):
            i = idx[0]
            keep.append(int(i))
            if len(idx) == 1:
                break
            iou = iou_matrix(boxes[i : i + 1], boxes[idx[1:]])[0]
            idx = idx[1:][iou < thr]
    return keep


def match_tp(pred, conf, pcls, gt, gcls) -> np.ndarray:
    """(n_pred, 10) bool: попадание на IoU 0.5..0.95; жадно по убыванию conf, в классе."""
    tp = np.zeros((len(pred), 10), bool)
    if not len(pred) or not len(gt):
        return tp
    iou = iou_matrix(pred, gt) * (pcls[:, None] == gcls[None, :])
    order = np.argsort(-conf)
    for t, thr in enumerate(np.linspace(0.5, 0.95, 10)):
        used = np.zeros(len(gt), bool)
        for i in order:
            row = np.where(used, 0, iou[i])
            j = int(row.argmax())
            if row[j] >= thr:
                used[j] = True
                tp[i, t] = True
    return tp


def compute_map(stats: list[dict]) -> dict:
    """stats: по кадру {'pred','conf','pcls','gt','gcls'} -> mAP50 / mAP50-95 общие и по классам."""
    from ultralytics.utils.metrics import ap_per_class

    tps, confs, pcs, gcs = [], [], [], []
    for s in stats:
        tps.append(match_tp(s["pred"], s["conf"], s["pcls"], s["gt"], s["gcls"]))
        confs.append(s["conf"])
        pcs.append(s["pcls"])
        gcs.append(s["gcls"])
    tp = np.concatenate(tps) if tps else np.zeros((0, 10), bool)
    res = ap_per_class(tp, np.concatenate(confs), np.concatenate(pcs), np.concatenate(gcs))
    ap, ucls = res[5], res[6]
    classes = {
        PPE_CLASSES[int(c)]: {"mAP50": float(ap[i, 0]), "mAP50-95": float(ap[i].mean())}
        for i, c in enumerate(ucls)
    }
    n = max(len(classes), 1)
    return {
        "mAP50": sum(v["mAP50"] for v in classes.values()) / n,
        "mAP50-95": sum(v["mAP50-95"] for v in classes.values()) / n,
        "classes": classes,
    }


def _ppe_dets(model, imgs, imgsz, device):
    """Пакет изображений -> список (xyxy, conf, cls) только классов СИЗ."""
    out = []
    rs = model.predict(
        imgs, imgsz=imgsz, device=device, conf=0.001, iou=0.6, max_det=300, verbose=False
    )
    for r in rs:
        b = r.boxes
        xyxy, cf, cl = b.xyxy.cpu().numpy(), b.conf.cpu().numpy(), b.cls.cpu().numpy().astype(int)
        m = np.isin(cl, list(PPE_CLASSES))
        out.append((xyxy[m], cf[m], cl[m]))
    return out


def detect_full(model, img, imgsz, device):
    return _ppe_dets(model, [img], imgsz, device)[0]


def detect_crops(model, people, img, device, person_conf=0.3, imgsz=640):
    """Детектор людей -> вырезки -> СИЗ-детектор на каждой -> боксы в координатах кадра."""
    h, w = img.shape[:2]
    pr = people.predict(img, imgsz=640, device=device, conf=person_conf, classes=[0], verbose=False)
    wins = [person_crop(b, w, h) for b in pr[0].boxes.xyxy.cpu().numpy()]
    if not wins:
        return np.zeros((0, 4), np.float32), np.zeros(0, np.float32), np.zeros(0, int)
    dets = _ppe_dets(model, [img[y1:y2, x1:x2] for x1, y1, x2, y2 in wins], imgsz, device)
    bx = np.concatenate([boxes_to_frame(d[0], win) for d, win in zip(dets, wins, strict=True)])
    cf = np.concatenate([d[1] for d in dets])
    cl = np.concatenate([d[2] for d in dets])
    keep = nms_classwise(bx, cf, cl) if len(bx) else []
    return bx[keep], cf[keep], cl[keep]


def load_scenes(src: Path, target: float, limit: int | None):
    """[(холст, метки)] для кадров val с касками, равномерная выборка до limit."""
    imgs = sorted(p for p in (src / "images" / "val").iterdir() if p.suffix.lower() in IMG_EXT)
    scenes = []
    for p in imgs:
        lp = src / "labels" / "val" / (p.stem + ".txt")
        if not lp.exists():
            continue
        rows = [ln.split() for ln in lp.read_text().splitlines() if ln.strip()]
        if not any(r[0] == str(HELMET) for r in rows):
            continue
        scenes.append((p, np.array(rows, np.float32).reshape(-1, 5)))
    if limit and len(scenes) > limit:
        step = len(scenes) / limit
        scenes = [scenes[int(i * step)] for i in range(limit)]
    out = []
    for p, lab in scenes:
        im = cv2.imread(str(p))
        if im is None:
            continue
        r = shrink_scene(im, lab, target)
        if r:
            out.append((r[0], r[1], r[2]))
    return out


def evaluate(scenes, detect) -> dict:
    stats, times = [], []
    detect(scenes[0][0])  # прогрев
    for img, lab, _ in scenes:
        t = time.perf_counter()
        bx, cf, cl = detect(img)
        times.append(time.perf_counter() - t)
        keep = np.isin(lab[:, 0], list(PPE_CLASSES))
        stats.append(
            {
                "pred": bx,
                "conf": cf,
                "pcls": cl,
                "gt": xywhn_to_xyxy(lab[keep]),
                "gcls": lab[keep, 0].astype(int),
            }
        )
    r = compute_map(stats)
    r["ms_per_frame"] = 1000 * float(np.mean(times))
    r["frames"] = len(scenes)
    return r


def run(weights: dict, people_w: Path, src: Path, sizes, device, limit) -> dict:
    from ultralytics import YOLO

    people = YOLO(str(people_w))
    models = {n: YOLO(str(w)) for n, w in weights.items()}
    res = {}
    for size in sizes:
        scenes = load_scenes(src, size, limit)
        for mname, m in models.items():
            methods = {
                "full640": lambda im, m=m: detect_full(m, im, 640, device),
                "full960": lambda im, m=m: detect_full(m, im, 960, device),
                "crops": lambda im, m=m: detect_crops(m, people, im, device),
            }
            for meth, fn in methods.items():
                key = f"{mname}|{size}|{meth}"
                res[key] = evaluate(scenes, fn)
                print(
                    key, round(res[key]["mAP50"], 3), round(res[key]["ms_per_frame"], 1), flush=True
                )
    return res


def format_table(res: dict) -> str:
    classes = list(PPE_CLASSES.values())
    head = ["веса", "каска px", "способ", "mAP50", "mAP50-95"]
    head += [f"{c} 50 / 50-95" for c in classes] + ["мс/кадр"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for key, m in res.items():
        mname, size, meth = key.split("|")
        cells = [mname, size, meth, f"{m['mAP50']:.3f}", f"{m['mAP50-95']:.3f}"]
        for c in classes:
            v = m["classes"].get(c)
            cells.append(f"{v['mAP50']:.3f} / {v['mAP50-95']:.3f}" if v else "-")
        cells.append(f"{m['ms_per_frame']:.0f}")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--weights", type=Path, default=ML / "runs/release-ppe/best.pt")
    ap.add_argument("--weights2", type=Path, help="доп. веса (например ppe-v2)")
    ap.add_argument("--people", type=Path, default=ML.parent / "yolo11n.pt")
    ap.add_argument("--data", type=Path, default=ML / "data/ppe")
    ap.add_argument("--work", type=Path, default=ML / "runs/small_objects")
    ap.add_argument("--device", default="0")
    ap.add_argument("--sizes", type=float, nargs="*", default=[16, 24, 32])
    ap.add_argument("--limit", type=int, default=300)
    a = ap.parse_args(argv)
    w = {"v1": a.weights}
    if a.weights2:
        w["v2"] = a.weights2
    res = run(w, a.people, a.data, a.sizes, a.device, a.limit)
    a.work.mkdir(parents=True, exist_ok=True)
    (a.work / "result.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(format_table(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
