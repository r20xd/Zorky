"""Замер скорости инференса: FPS и задержка p50/p95 — для слайда «LinQ против CPU».

Из папки ml/ (нужен ml/requirements.txt):
    python -m export.bench export yolo11n.pt            # -> yolo11n.onnx
    python -m export.bench run yolo11n.onnx             # замер на CPU, печать строки таблицы
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections.abc import Callable

from worker.infer import DEVICES, DeviceUnavailable, load_model, make_session

STREAMS = (1, 2, 4)


def benchmark(infer: Callable[[], object], warmup: int = 10, iters: int = 100) -> dict[str, float]:
    """Прогоняет infer() warmup раз вхолостую, затем iters раз с замером времени."""
    for _ in range(warmup):
        infer()
    timings = []
    for _ in range(iters):
        started = time.perf_counter()
        infer()
        timings.append(time.perf_counter() - started)
    timings.sort()
    total = sum(timings)

    def percentile_ms(p: float) -> float:
        return timings[min(len(timings) - 1, round(p / 100 * (len(timings) - 1)))] * 1000

    return {
        "fps": len(timings) / total if total > 0 else float("inf"),
        "p50_ms": percentile_ms(50),
        "p95_ms": percentile_ms(95),
    }


def table_row(model: str, device: str, imgsz: int, result: dict[str, float]) -> str:
    """Строка таблицы замеров для отчёта."""
    return (
        f"| {model} | {device} | {imgsz} | {result['fps']:.1f} "
        f"| {result['p50_ms']:.1f} | {result['p95_ms']:.1f} |"
    )


def export_onnx(weights: str, imgsz: int = 640, opset: int | None = None) -> str:
    """YOLO .pt -> .onnx рядом с весами; возвращает путь к ONNX."""
    kwargs = {"opset": opset} if opset else {}
    return str(load_model(weights).export(format="onnx", imgsz=imgsz, **kwargs))


def make_cpu_infer(onnx_path: str, imgsz: int = 640) -> Callable[[], object]:
    """Один прогон ONNX на CPU по пустому кадру (onnxruntime)."""
    import numpy as np

    session = make_session(onnx_path, "cpu")
    name = session.get_inputs()[0].name
    batch = np.zeros((1, 3, imgsz, imgsz), dtype=np.float32)
    return lambda: session.run(None, {name: batch})


def _pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))] * 1000


def _stat(values: list[float]) -> dict[str, float]:
    return {"p50_ms": _pct(values, 50), "p95_ms": _pct(values, 95)}


def make_frame_steps(session, imgsz: int = 640, frame=None):
    """(pre, infer, post) для одного кадра; кадр по умолчанию - пустой 720p."""
    import numpy as np

    from worker.detect import decode, letterbox

    frame = frame if frame is not None else np.zeros((720, 1280, 3), dtype=np.uint8)
    name = session.get_inputs()[0].name

    def pre():
        return letterbox(frame, imgsz)

    def infer(prepared):
        return session.run(None, {name: prepared[0]})[0]

    def post(output, prepared):
        return decode(output, prepared[1], prepared[2], frame.shape[:2], 0.25)

    return pre, infer, post


def run_streams(steps, streams: int, frames: int = 50, warmup: int = 3) -> dict:
    """Параллельные потоки; кадр -> событие = пред + инференс + пост (с ожиданием в очереди)."""
    pre, infer, post = steps
    for _ in range(warmup):
        prepared = pre()
        post(infer(prepared), prepared)
    pres, infs, posts, totals = [], [], [], []
    lock = threading.Lock()

    def worker():
        local = ([], [], [], [])
        for _ in range(frames):
            t0 = time.perf_counter()
            prepared = pre()
            t1 = time.perf_counter()
            output = infer(prepared)
            t2 = time.perf_counter()
            post(output, prepared)
            t3 = time.perf_counter()
            for lst, v in zip(local, (t1 - t0, t2 - t1, t3 - t2, t3 - t0), strict=True):
                lst.append(v)
        with lock:
            for dst, src in zip((pres, infs, posts, totals), local, strict=True):
                dst.extend(src)

    threads = [threading.Thread(target=worker) for _ in range(streams)]
    started = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - started
    return {
        "streams": streams,
        "fps": len(totals) / wall if wall > 0 else float("inf"),
        "infer": _stat(infs),
        "pre": _stat(pres),
        "post": _stat(posts),
        "latency": _stat(totals),
    }


def bench_engine(
    engine: str,
    onnx_path: str,
    imgsz: int = 640,
    frames: int = 50,
    streams: tuple[int, ...] = STREAMS,
    session_factory: Callable[..., object] | None = None,
) -> dict:
    """Замер движка; недоступный движок (linq) даёт skipped с причиной, а не падение."""
    try:
        session = make_session(onnx_path, engine, session_factory)
    except DeviceUnavailable as exc:
        return {"engine": engine, "skipped": str(exc), "runs": []}
    steps = make_frame_steps(session, imgsz)
    return {
        "engine": engine,
        "skipped": None,
        "runs": [run_streams(steps, n, frames) for n in streams],
    }


FULL_HEADER = (
    "| движок | потоки | FPS | инференс p50, мс | инференс p95, мс | пред p50, мс "
    "| пост p50, мс | кадр->событие p50, мс | p95, мс |\n|---|---|---|---|---|---|---|---|---|"
)


def full_rows(result: dict) -> list[str]:
    if result["skipped"]:
        return [f"| {result['engine']} | - | пропущен: {result['skipped']} | | | | | | |"]
    return [
        f"| {result['engine']} | {r['streams']} | {r['fps']:.1f} | {r['infer']['p50_ms']:.1f} "
        f"| {r['infer']['p95_ms']:.1f} | {r['pre']['p50_ms']:.1f} | {r['post']['p50_ms']:.1f} "
        f"| {r['latency']['p50_ms']:.1f} | {r['latency']['p95_ms']:.1f} |"
        for r in result["runs"]
    ]


def bench_all(
    onnx_path: str,
    engines: tuple[str, ...] = DEVICES,
    out: str | None = None,
    session_factory: Callable[..., object] | None = None,
    **kwargs,
) -> list[str]:
    results = [
        bench_engine(e, onnx_path, session_factory=session_factory, **kwargs) for e in engines
    ]
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"model": onnx_path, "results": results}, f, ensure_ascii=False, indent=2)
    return [FULL_HEADER] + [row for r in results for row in full_rows(r)]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    exp = sub.add_parser("export", help="YOLO .pt -> ONNX")
    exp.add_argument("weights")
    exp.add_argument("--imgsz", type=int, default=640)
    exp.add_argument("--opset", type=int)
    run = sub.add_parser("run", help="замер ONNX на CPU")
    run.add_argument("onnx")
    run.add_argument("--imgsz", type=int, default=640)
    run.add_argument("--warmup", type=int, default=10)
    run.add_argument("--iters", type=int, default=100)
    full = sub.add_parser("all", help="cpu/gpu/linq x 1/2/4 потока, таблица и JSON")
    full.add_argument("onnx")
    full.add_argument("--imgsz", type=int, default=640)
    full.add_argument("--frames", type=int, default=50)
    full.add_argument("--engines", default=",".join(DEVICES))
    full.add_argument("--out", default="bench.json")
    args = parser.parse_args(argv)
    if args.cmd == "all":
        engines = tuple(args.engines.split(","))
        lines = bench_all(args.onnx, engines, args.out, imgsz=args.imgsz, frames=args.frames)
        print("\n".join(lines))
        return
    if args.cmd == "export":
        print(export_onnx(args.weights, args.imgsz, args.opset))
        return
    result = benchmark(make_cpu_infer(args.onnx, args.imgsz), args.warmup, args.iters)
    print(table_row(args.onnx.rsplit("/", 1)[-1], "CPU (onnxruntime)", args.imgsz, result))


if __name__ == "__main__":
    main()
