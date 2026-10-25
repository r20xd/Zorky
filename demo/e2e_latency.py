"""Сквозной замер: от кадра с нарушением до события на экране оператора.

    ml/.venv/Scripts/python demo/e2e_latency.py --device linq --people people.onnx \\
        --ppe ml/linq_programs/ppe16_b1_o5_h1m.tpu --clean clean.jpg --viol viol.jpg

Поднимается всё настоящее, кроме камеры: свой бэкенд на тестовом порту (чужая база не
трогается), воркер с моделью и поток MJPEG, в котором в известный момент чистый кадр
сменяется кадром с нарушением. Клиент SSE (`/api/stream`) ловит событие, как это делает
экран оператора. Три отметки времени (часы одного компьютера):

    t0      первый кадр с нарушением записан в поток
    ts      время события, которое поставил воркер (`ts` в теле события)
    t_sse   событие получено клиентом SSE

    до события = ts - t0 (чтение кадра, инференс, подтверждение нарушения, снимок)
    до экрана  = t_sse - ts (отправка, запись в журнал, рассылка)
    сквозное   = t_sse - t0

Первый заход помечается холодным (загрузка модели, прогрев) и в итог не входит.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAMERA = "e2e-cam"


class Stream:
    """Общее состояние потока: какой кадр отдаём сейчас и когда впервые отдали кадр с нарушением."""

    def __init__(self, clean: bytes, viol: bytes, fps: float) -> None:
        self.clean, self.viol, self.fps = clean, viol, fps
        self.violating = False
        self.first_viol_at: float | None = None
        self.lock = threading.Lock()

    def current(self) -> bytes:
        with self.lock:
            if self.violating:
                if self.first_viol_at is None:
                    self.first_viol_at = time.time()
                return self.viol
            return self.clean

    def start_violation(self) -> None:
        with self.lock:
            self.violating, self.first_viol_at = True, None

    def stop_violation(self) -> None:
        with self.lock:
            self.violating = False


def make_handler(stream: Stream):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                while True:
                    jpeg = stream.current()
                    self.wfile.write(
                        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(jpeg)).encode()
                        + b"\r\n\r\n"
                        + jpeg
                        + b"\r\n"
                    )
                    time.sleep(1 / stream.fps)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

    return Handler


class Sse(threading.Thread):
    """Клиент /api/stream: запоминает (имя события, время получения, тело)."""

    def __init__(self, url: str) -> None:
        super().__init__(daemon=True)
        self.url, self.items, self.lock = url, [], threading.Lock()

    def run(self) -> None:
        name = ""
        while True:
            try:
                with urllib.request.urlopen(self.url, timeout=300) as response:
                    for raw in response:
                        line = raw.decode("utf-8", "replace").rstrip("\r\n")
                        if line.startswith("event:"):
                            name = line[6:].strip()
                        elif line.startswith("data:"):
                            try:
                                body = json.loads(line[5:].strip())
                            except ValueError:
                                continue
                            with self.lock:
                                self.items.append((name or "message", time.time(), body))
            except OSError:
                time.sleep(1)

    def first_after(self, t0: float, camera: str):
        with self.lock:
            for name, at, body in self.items:
                if at >= t0 and name in ("violation", "update") and body.get("camera_id") == camera:
                    return name, at, body
        return None


def parse_ts(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def summarize(trials: list[dict]) -> dict:
    """Перцентили по удачным заходам, кроме холодного; промахи считаются отдельно."""
    warm = [t for t in trials if not t["cold"]]
    hits = [t for t in warm if t.get("total_ms") is not None]

    def pct(key: str) -> dict | None:
        values = sorted(t[key] for t in hits)
        if not values:
            return None
        return {
            "p50": round(statistics.median(values), 0),
            "p95": round(values[min(len(values) - 1, int(0.95 * len(values)))], 0),
            "max": round(values[-1], 0),
            "min": round(values[0], 0),
        }

    return {
        "trials": len(warm),
        "detected": len(hits),
        "missed": len(warm) - len(hits),
        "to_event_ms": pct("to_event_ms"),
        "to_screen_ms": pct("to_screen_ms"),
        "total_ms": pct("total_ms"),
    }


def wait_http(url: str, seconds: float = 90.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2).read()
            return
        except OSError:
            time.sleep(1)
    raise SystemExit(f"{url} не отвечает")


def python_of(folder: str) -> str:
    for rel in ("Scripts/python.exe", "bin/python"):
        path = ROOT / folder / ".venv" / rel
        if path.is_file():
            return str(path)
    return sys.executable


def sleep_alive(seconds: float, alive) -> None:
    """Спит, но сразу падает, если воркер умер: иначе замер ждёт события, которых не будет."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        alive()
        time.sleep(0.5)


def run_trials(
    stream: Stream, sse: Sse, trials: int, clean_s: float, warm_s: float, wait_s: float, alive=None
) -> list[dict]:
    alive = alive or (lambda: None)
    out: list[dict] = []
    sleep_alive(warm_s, alive)  # модель загружена, камера крутит чистые кадры
    for k in range(trials):
        stream.stop_violation()
        sleep_alive(clean_s, alive)
        stream.start_violation()
        while stream.first_viol_at is None:
            time.sleep(0.05)
        t0 = stream.first_viol_at
        deadline = time.time() + wait_s
        found = None
        while time.time() < deadline and found is None:
            alive()
            found = sse.first_after(t0, CAMERA)
            time.sleep(0.05)
        row: dict = {"trial": k + 1, "cold": k == 0}
        if found is None:
            row["missed"] = True
            print(f"заход {k + 1}: события нет за {wait_s:.0f} с", flush=True)
        else:
            name, at, body = found
            ts = parse_ts(body["ts"])
            row.update(
                event=name,
                type=body.get("type"),
                to_event_ms=round((ts - t0) * 1000),
                to_screen_ms=round((at - ts) * 1000),
                total_ms=round((at - t0) * 1000),
            )
            print(
                f"заход {k + 1}{' (холодный)' if k == 0 else ''}: {body.get('type')}, "
                f"до события {row['to_event_ms']} мс, до экрана {row['to_screen_ms']} мс, "
                f"сквозное {row['total_ms']} мс",
                flush=True,
            )
        out.append(row)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", choices=("cpu", "linq"), required=True)
    parser.add_argument("--people", required=True, help="модель людей .onnx (COCO, CPU)")
    parser.add_argument("--ppe", required=True, help="модель СИЗ: .onnx для cpu, .tpu для linq")
    parser.add_argument("--clean", type=Path, required=True, help="кадр без нарушения (jpg)")
    parser.add_argument("--viol", type=Path, required=True, help="кадр с нарушением (jpg)")
    parser.add_argument("--trials", type=int, default=6)
    parser.add_argument("--clean-s", type=float, default=20.0)
    parser.add_argument("--warm-s", type=float, default=15.0)
    parser.add_argument("--wait-s", type=float, default=90.0)
    parser.add_argument("--fps", type=float, default=5.0, help="частота кадров камеры")
    parser.add_argument("--edge-queue", default="", help="linq: очередь сервиса кадров на стенде")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    # воркер запускается из ml/: относительные пути к моделям там не найдутся
    args.people, args.ppe = (str(Path(v).resolve()) for v in (args.people, args.ppe))

    backend_port, stream_port = 18090, 18091
    backend_url = f"http://127.0.0.1:{backend_port}"
    scratch = Path(tempfile.mkdtemp(prefix="mosprom-e2e-latency-"))
    env = {k: v for k, v in os.environ.items() if k not in ("WORKER_TOKEN", "MOSPROM_USERS")}
    env.update(MOSPROM_DB=str(scratch / "db.sqlite"), MOSPROM_SNAPSHOTS=str(scratch / "snaps"))
    procs: list[subprocess.Popen] = []
    server = None
    try:
        procs.append(
            subprocess.Popen(
                [python_of("backend"), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1"]
                + ["--port", str(backend_port)],
                cwd=ROOT / "backend",
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
        wait_http(f"{backend_url}/healthz")
        stream = Stream(args.clean.read_bytes(), args.viol.read_bytes(), args.fps)
        server = ThreadingHTTPServer(("127.0.0.1", stream_port), make_handler(stream))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        sse = Sse(f"{backend_url}/api/stream")
        sse.start()

        worker_env = dict(env)
        if args.device == "linq":
            worker_env.update(LINQ_MODE="edge", LINQ_EDGE_QUEUE=args.edge_queue)
        command = [
            python_of("ml"),
            "-m",
            "worker.main",
            "--camera",
            CAMERA,
            "--device",
            args.device,
        ]
        command += ["--video", f"http://127.0.0.1:{stream_port}/live.mjpg", "--fps", "4"]
        command += ["--model", args.people, "--ppe-weights", args.ppe, "--backend", backend_url]
        log = (scratch / "worker.log").open("wb")
        procs.append(
            subprocess.Popen(
                command, cwd=ROOT / "ml", env=worker_env, stdout=log, stderr=subprocess.STDOUT
            )
        )
        print(f"устройство: {args.device}; воркер и бэкенд запущены, прогрев…", flush=True)
        worker = procs[-1]

        def alive() -> None:
            if worker.poll() is not None:
                log.flush()
                tail = (scratch / "worker.log").read_text(encoding="utf-8", errors="replace")
                raise SystemExit(f"воркер завершился (код {worker.returncode}):\n{tail[-800:]}")

        trials = run_trials(stream, sse, args.trials, args.clean_s, args.warm_s, args.wait_s, alive)
        report = {
            "device": args.device,
            "frame_rate": args.fps,
            "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "note": "бэкенд, воркер и клиент SSE на одном компьютере; нарушение - смена кадра в "
            "потоке MJPEG; ts - время события из воркера",
            "summary": summarize(trials),
            "trials": trials,
        }
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        tail = (scratch / "worker.log").read_text(encoding="utf-8", errors="replace")[-600:]
        if report["summary"]["detected"] == 0:
            print("журнал воркера:\n" + tail)
            return 1
        return 0
    finally:
        if server is not None:
            server.shutdown()
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
