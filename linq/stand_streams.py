"""Замер LinQ на 1, 2 и 4 потока (камеры) прямо на стенде, в терминале Jupyter.

    python3 stand_streams.py <логин> <программа.tpu> (--npz кадры.npz | --image кадр.jpg)
            [--streams 1,2,4] [--fps 10] [--seconds 20] [--window 2] [--json-out отчёт.json]

Поток = своё соединение с RabbitMQ и своя очередь ответов; кадры идут с частотой камеры `--fps`.
Если ускоритель не успевает и в полёте уже `--window` запросов, кадр пропускается (так делает
живая камера), и пропуски считаются. `--fps 0` - режим насыщения: шлём, как только есть место.
Для каждого числа потоков: FPS суммарно и на поток, p50/p95/max задержки, пропуски, потери.
В отчёт пишутся условия: хеш и версия SDK из <программа>.tpu.json, размер, batch, прогрев,
время и загрузка стенда (loadavg) - стенд общий, цифры плавают, при разбросе замер повторяют.
Только сеть стенда: канал до ноутбука не участвует. Печатает строку `STREAMS {json}`.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path

import numpy as np

DRAIN_S = 15.0  # сколько после конца окна ждём отставшие ответы; не пришли - потеряны
WARMUP_S = 180.0  # первая загрузка программы в ускоритель может быть долгой


def percentile(values: list[float], q: float) -> float:
    return float(np.percentile(np.array(values), q))


def summarize(latencies: list[float], sent: int, dropped: int, lost: int, seconds: float) -> dict:
    """Итог одного потока. latencies - мс по принятым ответам."""
    done = len(latencies)
    lat = (
        {
            "mean": round(float(np.mean(latencies)), 1),
            "p50": round(percentile(latencies, 50), 1),
            "p95": round(percentile(latencies, 95), 1),
            "max": round(max(latencies), 1),
        }
        if latencies
        else None
    )
    offered = sent + dropped
    return {
        "offered": offered,
        "sent": sent,
        "done": done,
        "dropped": dropped,
        "lost": lost,
        "drop_rate": round(dropped / offered, 3) if offered else None,
        "fps": round(done / seconds, 2) if seconds > 0 else None,
        "latency_ms": lat,
    }


def run_stream(
    client,
    tensor: np.ndarray,
    fps: float,
    seconds: float,
    window: int,
    clock=time.monotonic,
    sleep=time.sleep,
) -> dict:
    """Один поток. client: send(tensor) -> id и poll() -> [id, ...] (без ожидания)."""
    period = 1.0 / fps if fps > 0 else 0.0
    pending: dict[str, float] = {}
    latencies: list[float] = []
    sent = dropped = 0

    def collect() -> None:
        for task_id in client.poll():
            started = pending.pop(task_id, None)
            if started is not None:
                latencies.append((clock() - started) * 1000)

    start = clock()
    end = start + seconds
    next_tick = start
    while clock() < end:
        collect()
        now = clock()
        if now >= next_tick and (period or len(pending) < window):
            if len(pending) < window:
                pending[client.send(tensor)] = clock()
                sent += 1
            else:
                dropped += 1
            next_tick = next_tick + period
        else:
            sleep(0.0005)
    drain_end = clock() + DRAIN_S
    while pending and clock() < drain_end:
        collect()
        sleep(0.001)
    return summarize(latencies, sent, dropped, len(pending), seconds)


def aggregate(per_stream: list[dict], seconds: float) -> dict:
    """Сумма по потокам: суммарный FPS, худшие p95 и max, все пропуски."""
    lats = [s["latency_ms"] for s in per_stream if s["latency_ms"]]
    offered = sum(s["offered"] for s in per_stream)
    dropped = sum(s["dropped"] for s in per_stream)
    done = sum(s["done"] for s in per_stream)
    return {
        "streams": len(per_stream),
        "total_fps": round(done / seconds, 2) if seconds > 0 else None,
        "fps_per_stream": round(done / seconds / len(per_stream), 2) if per_stream else None,
        "done": done,
        "dropped": dropped,
        "lost": sum(s["lost"] for s in per_stream),
        "drop_rate": round(dropped / offered, 3) if offered else None,
        "p50_ms_worst_stream": max((x["p50"] for x in lats), default=None),
        "p95_ms_worst_stream": max((x["p95"] for x in lats), default=None),
        "max_ms": max((x["max"] for x in lats), default=None),
        "per_stream": per_stream,
    }


def run_streams(clients: list, tensors: list[np.ndarray], fps, seconds, window) -> dict:
    """Все потоки одновременно: свой поток Python на клиента, общий старт по барьеру."""
    barrier = threading.Barrier(len(clients))
    results: list = [None] * len(clients)
    errors: list = []

    def work(index: int) -> None:
        try:
            barrier.wait()
            results[index] = run_stream(clients[index], tensors[index], fps, seconds, window)
        except threading.BrokenBarrierError:
            pass  # другой поток упал раньше: причина в errors
        except Exception as exc:  # noqa: BLE001 - отчёт важнее: покажем причину, а не упадём
            errors.append(f"поток {index}: {exc!r}")
            barrier.abort()

    threads = [threading.Thread(target=work, args=(i,)) for i in range(len(clients))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors or any(r is None for r in results):
        raise RuntimeError("; ".join(errors) or "поток не вернул результат")
    return aggregate(results, seconds)


class StandClient:
    """Клиент стенда с неблокирующим приёмом: send -> id, poll -> [id, ...]."""

    def __init__(self, login: str, program: str, input_name: str, output_name: str):
        try:
            from stand_bench import Client
        except ImportError:  # запуск из корня репозитория
            from linq.stand_bench import Client
        from tpu_pycloud_format import MESSAGE_ID_ATTR_NAME, deserialize

        self._client = Client(login, program, input_name, output_name)
        self._id_attr, self._deserialize = MESSAGE_ID_ATTR_NAME, deserialize

    def send(self, tensor: np.ndarray) -> str:
        return self._client.send(tensor)

    def poll(self) -> list[str]:
        ids = []
        while True:
            method, props, body = self._client.ch.basic_get(self._client.reply, auto_ack=True)
            if method is None:
                return ids
            data, _meta, _worker = self._deserialize(body)
            if data:  # пустой ответ - ошибка воркера: не считаем кадр обработанным
                ids.append(props.headers[self._id_attr])

    def warmup(self, tensor: np.ndarray) -> None:
        self.send(tensor)
        deadline = time.monotonic() + WARMUP_S
        while time.monotonic() < deadline:
            if self.poll():
                return
            time.sleep(0.05)
        raise TimeoutError("прогрев: программа не ответила")

    def close(self) -> None:
        self._client.close()


def load_tensors(args: argparse.Namespace) -> list[np.ndarray]:
    if args.npz:
        data = np.load(args.npz)["tensors"].astype(np.float32)
        return [t[None] for t in data]
    try:
        from stand_bench import preprocess_letterbox
    except ImportError:
        from linq.stand_bench import preprocess_letterbox
    return [preprocess_letterbox(args.image, 640)]


def conditions(login: str, program: str, args: argparse.Namespace) -> dict:
    """Условия замера по протоколу ТЗ: модель/хеш, batch, размер, precision, версия SDK, прогрев."""
    meta_path = Path(f"/auto/cache/{login}") / (program + ".json")
    meta = json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else {}
    load = os.getloadavg() if hasattr(os, "getloadavg") else None
    return {
        "program": program,
        "sha256": meta.get("sha256"),
        "sdk_version": meta.get("sdk_version"),
        "preset": meta.get("preset"),
        "batch": meta.get("batch", 1),
        "input_size": 640,
        "precision": "int8",
        "warmup": "1 кадр на поток, не входит в окно",
        "fps_offered_per_stream": args.fps or "насыщение",
        "window_per_stream": args.window,
        "seconds": args.seconds,
        "loadavg_at_start": load,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "note": "стенд общий, скорость плавает: при большом разбросе повторить в другое время",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("login")
    parser.add_argument("program")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--npz", help="тензоры из stand_accuracy.py prep (поле tensors)")
    source.add_argument("--image", help="один кадр, letterbox 640")
    parser.add_argument("--streams", default="1,2,4")
    parser.add_argument("--fps", type=float, default=10.0, help="кадров/с на поток; 0 - насыщение")
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--window", type=int, default=2, help="запросов в полёте на поток")
    parser.add_argument("--json-out")
    args = parser.parse_args()

    counts = [int(x) for x in args.streams.split(",")]
    base = load_tensors(args)
    meta_path = Path(f"/auto/cache/{args.login}") / (args.program + ".json")
    meta = json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else {}
    input_name = meta.get("input", "images")
    output_name = (meta.get("outputs") or ["output0"])[0]

    report = {"conditions": conditions(args.login, args.program, args), "runs": []}
    for count in counts:
        clients = [
            StandClient(args.login, args.program, input_name, output_name) for _ in range(count)
        ]
        try:
            # прогрев последовательно, чтобы загрузка программы не попала в окно
            for client in clients:
                client.warmup(base[0])
            tensors = [base[i % len(base)] for i in range(count)]
            run = run_streams(clients, tensors, args.fps, args.seconds, args.window)
            report["runs"].append(run)
        finally:
            for client in clients:
                client.close()
    print("STREAMS " + json.dumps(report, ensure_ascii=False))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
