"""Замер инференса на LinQ прямо на стенде (в терминале Jupyter, протокол организаторов).

    python3 stand_bench.py <логин> <программа.tpu> <картинка> [--runs 30] [--inflight 4]
            [--input input.1] [--output 495] [--size 224]

По умолчанию — образец организаторов (ResNet50). Печатает одну строку `BENCH {json}`:
задержка одного запроса (p50/p95/mean), пропускная способность при `inflight` запросах
одновременно, статус воркера TPU. Только сеть стенда — канал до ноутбука не участвует.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid

import numpy as np
import pika
from PIL import Image
from tpu_pycloud_format import (
    MESSAGE_ID_ATTR_NAME,
    RESPONSE_ROUTING_KEYS_ATTR_NAME,
    TARGET_URI_ATTR_NAME,
    PyTask,
    deserialize,
    serialize,
)

HOST, PORT, EXCHANGE, INPUT_QUEUE = "10.0.114.30", 5672, "tpu.direct", "input"


def preprocess_letterbox(path: str, size: int) -> np.ndarray:
    """YOLO: letterbox size x size с серыми полями 114, RGB, float32 0..1 (как в воркере)."""
    image = Image.open(path).convert("RGB")
    w, h = image.size
    scale = min(size / h, size / w)
    nw, nh = round(w * scale), round(h * scale)
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    canvas.paste(image.resize((nw, nh)), ((size - nw) // 2, (size - nh) // 2))
    arr = np.asarray(canvas, dtype=np.float32) / 255.0
    return np.ascontiguousarray(np.transpose(arr, (2, 0, 1))[None])


def preprocess(path: str, size: int) -> np.ndarray:
    image = Image.open(path).convert("RGB")
    w, h = image.size
    m = int(min(w, h) * 0.85)
    left, top = (w - m) // 2, (h - m) // 2
    image = image.crop((left, top, left + m, top + m)).resize((size, size))
    arr = (np.asarray(image) / 255.0 - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
    return np.transpose(arr, (2, 0, 1))[None].astype(np.float32)


class Client:
    def __init__(self, login: str, program: str, input_name: str, output_name: str):
        self.target = f"file:///auto/cache/{login}/{program}?raw=False"
        self.input_name, self.output_name = input_name, output_name
        self.conn = pika.BlockingConnection(
            pika.ConnectionParameters(
                HOST, PORT, credentials=pika.PlainCredentials("guest", "guest")
            )
        )
        self.ch = self.conn.channel()
        self._messages = None
        self.reply = f"mosprom_{uuid.uuid4().hex[:8]}"
        self.ch.queue_declare(queue=self.reply, auto_delete=False)
        self.ch.queue_bind(exchange=EXCHANGE, queue=self.reply, routing_key=self.reply)

    def send(self, tensor: np.ndarray) -> str:
        task_id = uuid.uuid4().hex
        self.ch.basic_publish(
            exchange=EXCHANGE,
            routing_key=INPUT_QUEUE,
            body=serialize(PyTask({self.input_name: tensor})),
            properties=pika.BasicProperties(
                headers={
                    TARGET_URI_ATTR_NAME: self.target,
                    RESPONSE_ROUTING_KEYS_ATTR_NAME: json.dumps([self.reply]),
                    MESSAGE_ID_ATTR_NAME: task_id,
                }
            ),
        )
        return task_id

    def receive(self, timeout: float = 60.0):
        """Следующий ответ. Один постоянный consumer: после отмены теряются доставленные ответы."""
        if self._messages is None:
            self._messages = self.ch.consume(self.reply, auto_ack=True, inactivity_timeout=1.0)
        deadline = time.monotonic() + timeout
        for method, props, body in self._messages:
            if method is None:
                if time.monotonic() > deadline:
                    raise TimeoutError("нет ответа")
                continue
            data, _meta, worker = deserialize(body)
            if not data:
                raise RuntimeError(f"ошибка воркера: {worker.get('error_message')}")
            return props.headers[MESSAGE_ID_ATTR_NAME], data[self.output_name], worker

    def close(self):
        try:
            self.ch.cancel()
            self.ch.queue_delete(queue=self.reply)
        finally:
            self.conn.close()


def percentile(values: list[float], q: float) -> float:
    return float(np.percentile(np.array(values), q))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("login")
    parser.add_argument("program")
    parser.add_argument("image")
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--inflight", type=int, default=4)
    parser.add_argument("--input", default="input.1")
    parser.add_argument("--output", default="495")
    parser.add_argument("--size", type=int, default=224)
    parser.add_argument(
        "--yolo", action="store_true", help="вход YOLO: letterbox 0..1, без ImageNet-нормы"
    )
    parser.add_argument("--json-out", help="дописать отчёт в этот JSON-файл (список)")
    args = parser.parse_args()

    tensor = (preprocess_letterbox if args.yolo else preprocess)(args.image, args.size)
    client = Client(args.login, args.program, args.input, args.output)
    try:
        client.send(tensor)  # прогрев: загрузка программы в ускоритель
        _, scores, worker = client.receive()
        scores = np.asarray(scores)
        top = int(np.argmax(scores)) + 1
        out_shape = list(scores.shape)
        latencies = []
        for _ in range(args.runs):
            started = time.monotonic()
            client.send(tensor)
            client.receive()
            latencies.append((time.monotonic() - started) * 1000)
        pending, done = set(), 0
        started = time.monotonic()
        for _ in range(args.runs * 2):
            if len(pending) >= args.inflight:
                pending.discard(client.receive()[0])
                done += 1
            pending.add(client.send(tensor))
        while pending:
            pending.discard(client.receive()[0])
            done += 1
        elapsed = time.monotonic() - started
    finally:
        client.close()
    report = {
        "program": args.program,
        "top1_class": top,
        "output_shape": out_shape,
        "runs": args.runs,
        "latency_ms": {
            "mean": round(float(np.mean(latencies)), 1),
            "p50": round(percentile(latencies, 50), 1),
            "p95": round(percentile(latencies, 95), 1),
            "min": round(min(latencies), 1),
        },
        "single_fps": round(1000 / float(np.mean(latencies)), 1),
        "inflight": args.inflight,
        "pipelined_fps": round(done / elapsed, 1),
        "worker": {k: worker.get(k) for k in ("worker_pid", "status_code")},
    }
    print("BENCH " + json.dumps(report, ensure_ascii=False))
    if args.json_out:
        from pathlib import Path

        path = Path(args.json_out)
        old = json.loads(path.read_text()) if path.exists() else []
        path.write_text(json.dumps(old + [report], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
