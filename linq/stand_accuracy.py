"""Качество и скорость нашей модели на LinQ против эталона ONNX (FP32), прямо на стенде.

Успешная компиляция не доказывает качество, поэтому
одни и те же кадры и одна и та же пред/постобработка (ml/worker/detect.py) идут через оба пути.
На стенде два окружения: у venv есть onnxruntime и cv2, у системного python3 - pika и
tpu_pycloud_format. Поэтому два шага, связанные файлом npz:

    venv/bin/python stand_accuracy.py prep  <папка_кадров> <best.onnx> <out.npz>
    python3        stand_accuracy.py run   <логин> <программа.tpu> <in.npz> <out.json> [--conf 0.3]

`prep`: letterbox 640 и прогон ONNX (onnxruntime, CPU). `run`: те же тензоры - на ускоритель;
затем сравнение детекций (класс совпал, IoU >= 0.5) и скорость при 1, 2 и 4 запросах в полёте.
Рядом должен лежать detect.py из ml/worker/. Пишет один JSON-отчёт и строку `ACCURACY {...}`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

SIZE = 640
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def prep(frames_dir: str, onnx_path: str, out: str) -> None:
    import cv2
    import onnxruntime as ort
    from detect import letterbox

    options = ort.SessionOptions()
    options.intra_op_num_threads = 4  # в контейнере стенда лишние потоки только мешают
    session = ort.InferenceSession(onnx_path, options, providers=["CPUExecutionProvider"])
    name = session.get_inputs()[0].name
    tensors, outputs, scales, pads, shapes, files = [], [], [], [], [], []
    for path in sorted(Path(frames_dir).glob("*.jpg")):
        frame = cv2.imread(str(path))
        tensor, scale, pad = letterbox(frame, SIZE)
        outputs.append(session.run(None, {name: tensor})[0][0])
        tensors.append(tensor[0])
        scales.append(scale)
        pads.append(pad)
        shapes.append(frame.shape[:2])
        files.append(path.name)
        print(f"PREP {len(files)}", flush=True)
    np.savez(
        out,
        tensors=np.stack(tensors),
        onnx=np.stack(outputs),
        scales=np.array(scales),
        pads=np.array(pads),
        shapes=np.array(shapes),
        files=np.array(files),
    )
    print(f"PREP кадров: {len(files)} -> {out}")


def pairs(a, b, iou_min: float = 0.5):
    """Сколько детекций из `a` нашлось в `b` (класс тот же, IoU >= iou_min), один-к-одному."""
    from detect import iou_matrix

    (boxes_a, _sa, cls_a), (boxes_b, _sb, cls_b) = a, b
    if not len(boxes_a) or not len(boxes_b):
        return 0, []
    matrix = iou_matrix(boxes_a, boxes_b)
    taken: set[int] = set()
    ious = []
    for i in np.argsort(-matrix.max(axis=1)):
        order = np.argsort(-matrix[i])
        for j in order:
            if matrix[i, j] < iou_min:
                break
            if int(j) in taken or cls_a[i] != cls_b[j]:
                continue
            taken.add(int(j))
            ious.append(float(matrix[i, j]))
            break
    return len(ious), ious


def agreement(data, tpu, ref_conf: float, tpu_conf: float) -> dict:
    """Детекции LinQ при tpu_conf против ONNX FP32 при ref_conf: полнота, точность, F1, IoU."""
    from detect import decode

    ref_n = tpu_n = hit_ref = hit_tpu = frames_ref = frames_tpu = 0
    ious: list[float] = []
    for i in range(len(tpu)):
        geometry = (float(data["scales"][i]), tuple(data["pads"][i]), tuple(data["shapes"][i]))
        ref = decode(data["onnx"][i], *geometry, ref_conf)
        got = decode(tpu[i], *geometry, tpu_conf)
        ref_n += len(ref[0])
        tpu_n += len(got[0])
        frames_ref += bool(len(ref[0]))
        frames_tpu += bool(len(got[0]))
        matched, iou_list = pairs(ref, got)
        hit_ref += matched
        hit_tpu += pairs(got, ref)[0]
        ious += iou_list
    recall = hit_ref / ref_n if ref_n else None
    precision = hit_tpu / tpu_n if tpu_n else None
    f1 = 2 * precision * recall / (precision + recall) if recall and precision else None
    return {
        "detections_onnx": ref_n,
        "detections_linq": tpu_n,
        "frames_with_detections": {"onnx": frames_ref, "linq": frames_tpu},
        "recall": None if recall is None else round(recall, 3),
        "precision": None if precision is None else round(precision, 3),
        "f1": None if f1 is None else round(f1, 3),
        "mean_iou": round(float(np.mean(ious)), 3) if ious else None,
    }


def run(login: str, program: str, npz: str, out: str, conf: float) -> None:
    from detect import decode
    from stand_bench import Client

    data = np.load(npz)
    tensors = data["tensors"].astype(np.float32)
    meta = json.loads((Path(f"/auto/cache/{login}") / (program + ".json")).read_text("utf-8"))
    client = Client(login, program, meta["input"], meta["outputs"][0])
    try:
        client.send(tensors[:1][0][None])  # прогрев: загрузка программы в ускоритель
        client.receive()
        tpu, latencies = [], []
        for tensor in tensors:
            started = time.monotonic()
            client.send(tensor[None])
            _id, result, _worker = client.receive()
            latencies.append((time.monotonic() - started) * 1000)
            tpu.append(np.asarray(result)[0])
        throughput = {}
        for inflight in (1, 2, 4, 8):
            pending, done, started = set(), 0, time.monotonic()
            for k in range(len(tensors) * 2):
                if len(pending) >= inflight:
                    pending.discard(client.receive()[0])
                    done += 1
                pending.add(client.send(tensors[k % len(tensors)][None]))
            while pending:
                pending.discard(client.receive()[0])
                done += 1
            throughput[str(inflight)] = round(done / (time.monotonic() - started), 2)
    finally:
        client.close()

    np.savez(out + ".tpu.npz", tpu=np.stack(tpu))
    sweep = {}
    for tpu_conf in sorted({conf, 0.4, 0.5, 0.6, 0.7}):
        sweep[str(tpu_conf)] = agreement(data, tpu, conf, tpu_conf)
    onnx_classes: dict[int, int] = {}
    for i in range(len(tensors)):
        ref = decode(
            data["onnx"][i],
            float(data["scales"][i]),
            tuple(data["pads"][i]),
            tuple(data["shapes"][i]),
            conf,
        )
        for c in ref[2]:
            onnx_classes[int(c)] = onnx_classes.get(int(c), 0) + 1
    report = {
        "program": program,
        "frames": int(len(tensors)),
        "reference": f"onnx_fp32, conf {conf}",
        "agreement_by_tpu_conf": sweep,
        "latency_ms": {
            "mean": round(float(np.mean(latencies)), 1),
            "p50": round(float(np.percentile(latencies, 50)), 1),
            "p95": round(float(np.percentile(latencies, 95)), 1),
            "min": round(float(np.min(latencies)), 1),
        },
        "single_fps": round(1000 / float(np.mean(latencies)), 2),
        "fps_by_inflight": throughput,
        "onnx_detections_by_class": {str(k): v for k, v in sorted(onnx_classes.items())},
    }
    Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("ACCURACY " + json.dumps(report, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="stage", required=True)
    p = sub.add_parser("prep")
    p.add_argument("frames")
    p.add_argument("onnx")
    p.add_argument("out")
    r = sub.add_parser("run")
    r.add_argument("login")
    r.add_argument("program")
    r.add_argument("npz")
    r.add_argument("out")
    r.add_argument("--conf", type=float, default=0.3)
    args = parser.parse_args()
    if args.stage == "prep":
        prep(args.frames, args.onnx, args.out)
    else:
        run(args.login, args.program, args.npz, args.out, args.conf)


if __name__ == "__main__":
    main()
