"""Сервис на стенде: JPEG от воркера -> ускоритель LinQ -> только найденные рамки обратно.

Через VPN (~0,2 МБ/с) тензор 640x640 float32 (4,9 МБ) не проходит, а JPEG в десятки КБ проходит.
Воркер на ноутбуке шлёт сжатый кадр в очередь брокера стенда, этот сервис (на самом стенде,
где до ускорителя рукой подать) разжимает его, гонит через программу .tpu и возвращает не весь
выход (300 КБ), а несколько строк с рамками - в том же формате, который ждёт `decode`.

    python3 edge_service.py <логин> <программа.tpu> [--threads 3] [--min-conf 0.2]

Запускается на стенде системным python3 (там есть pika, tpu_pycloud_format, numpy и PIL),
обычно командой `py tasks.py linq-edge start`. Сообщение: тело - JPEG (кадр уже в letterbox
640x640, RGB), заголовки `id` и `reply` (имя очереди ответа). Ответ - float32 `[1, 4+nc, K]`
сырым телом, заголовки `id`, `shape` (JSON) и `ms` (JSON: разжатие, ускоритель, всего).
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import threading
import time
import uuid
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SIZE = 640
EXCHANGE, INPUT_QUEUE = "tpu.direct", "input"
HOST, PORT = "10.0.114.30", 5672


def edge_queue(login: str) -> str:
    return f"mosprom.edge.{login}"


def jpeg_to_tensor(body: bytes) -> np.ndarray:
    """JPEG letterbox-кадра -> float32 1x3x640x640, RGB, 0..1 (как `detect.letterbox`)."""
    from PIL import Image

    image = Image.open(io.BytesIO(body)).convert("RGB")
    if image.size != (SIZE, SIZE):
        image = image.resize((SIZE, SIZE))
    arr = np.asarray(image, dtype=np.float32) / 255.0
    return np.ascontiguousarray(arr.transpose(2, 0, 1)[None])


def compact(output: np.ndarray, min_conf: float, iou: float = 0.7) -> np.ndarray:
    """Выход модели (1, 4+nc, N) -> только рамки выше порога после NMS: (1, 4+nc, K).

    Формат строк тот же (центр, ширина, высота, оценки классов), поэтому на ноутбуке
    `decode` работает без изменений, а ответ весит сотни байт вместо 300 КБ.
    """
    try:
        from detect import nms  # на стенде detect.py лежит рядом с сервисом
    except ImportError:  # в репозитории он в ml/worker
        from worker.detect import nms

    pred = np.asarray(output)[0].T  # (N, 4+nc)
    scores = pred[:, 4:]
    classes, best = scores.argmax(axis=1), scores.max(axis=1)
    keep = best > min_conf
    pred, classes, best = pred[keep], classes[keep], best[keep]
    if not len(pred):
        return np.zeros((1, output.shape[1], 0), dtype=np.float32)
    boxes = np.stack(
        [
            pred[:, 0] - pred[:, 2] / 2,
            pred[:, 1] - pred[:, 3] / 2,
            pred[:, 0] + pred[:, 2] / 2,
            pred[:, 1] + pred[:, 3] / 2,
        ],
        axis=1,
    )
    kept = nms(boxes, best, classes, iou)[:300]
    return np.ascontiguousarray(pred[kept].T[None].astype(np.float32))


class Tpu:
    """Один клиент ускорителя: свой канал и своя очередь ответов (потоки не делят соединение)."""

    def __init__(self, pika, fmt, login: str, program: str, input_name: str, output: str):
        self.pika, self.fmt = pika, fmt
        self.target = f"file:///auto/cache/{login}/{program}?raw=False"
        self.input_name, self.output = input_name, output
        self.conn = pika.BlockingConnection(
            pika.ConnectionParameters(
                HOST, PORT, credentials=pika.PlainCredentials("guest", "guest"), heartbeat=30
            )
        )
        self.channel = self.conn.channel()
        self.reply = f"mosprom_edge_{uuid.uuid4().hex[:8]}"
        self.channel.queue_declare(queue=self.reply, auto_delete=False)
        self.channel.queue_bind(exchange=EXCHANGE, queue=self.reply, routing_key=self.reply)
        self.messages = self.channel.consume(self.reply, auto_ack=True, inactivity_timeout=1.0)

    def infer(self, tensor: np.ndarray, timeout: float = 60.0) -> np.ndarray:
        fmt = self.fmt
        task_id = uuid.uuid4().hex
        self.channel.basic_publish(
            exchange=EXCHANGE,
            routing_key=INPUT_QUEUE,
            body=fmt.serialize(fmt.PyTask({self.input_name: tensor})),
            properties=self.pika.BasicProperties(
                headers={
                    fmt.TARGET_URI_ATTR_NAME: self.target,
                    fmt.RESPONSE_ROUTING_KEYS_ATTR_NAME: json.dumps([self.reply]),
                    fmt.MESSAGE_ID_ATTR_NAME: task_id,
                }
            ),
        )
        deadline = time.monotonic() + timeout
        for method, props, body in self.messages:
            if method is None:
                if time.monotonic() > deadline:
                    raise TimeoutError("ускоритель не ответил")
                continue
            if props.headers.get(fmt.MESSAGE_ID_ATTR_NAME) != task_id:
                continue
            data, _meta, worker = fmt.deserialize(body)
            if not data:
                raise RuntimeError(f"ошибка ускорителя: {worker.get('error_message')}")
            return np.asarray(data[self.output])
        raise RuntimeError("очередь ответов закрыта")

    def close(self) -> None:
        try:
            self.channel.queue_delete(queue=self.reply)
        finally:
            self.conn.close()


def serve(login: str, program: str, min_conf: float, stop: threading.Event) -> None:
    """Один поток-обработчик: свой клиент ускорителя и своё соединение для чтения очереди."""
    import pika
    import tpu_pycloud_format as fmt

    meta = json.loads(Path(f"/auto/cache/{login}/{program}.json").read_text(encoding="utf-8"))
    tpu = Tpu(pika, fmt, login, program, meta["input"], meta["outputs"][0])
    conn = pika.BlockingConnection(
        pika.ConnectionParameters(
            HOST, PORT, credentials=pika.PlainCredentials("guest", "guest"), heartbeat=30
        )
    )
    channel = conn.channel()
    queue = edge_queue(login)
    channel.queue_declare(queue=queue, auto_delete=False)
    channel.basic_qos(prefetch_count=1)
    handled = 0
    try:
        for method, props, body in channel.consume(queue, inactivity_timeout=1.0):
            if stop.is_set():
                break
            if method is None:
                continue
            headers = props.headers or {}
            started = time.perf_counter()
            try:
                tensor = jpeg_to_tensor(body)
                decoded = time.perf_counter()
                output = tpu.infer(tensor)
                inferred = time.perf_counter()
                reply = compact(output, min_conf)
                answer = {"shape": list(reply.shape), "body": reply.tobytes()}
                error = ""
            except Exception as exc:  # noqa: BLE001 - любой сбой кадра: ответить ошибкой
                decoded = inferred = time.perf_counter()
                answer, error = {"shape": [1, 0, 0], "body": b""}, str(exc)[:200]
            done = time.perf_counter()
            ms = {
                "decode": round((decoded - started) * 1000, 1),
                "tpu": round((inferred - decoded) * 1000, 1),
                "total": round((done - started) * 1000, 1),
            }
            channel.basic_publish(
                exchange="",
                routing_key=headers.get("reply", ""),
                body=answer["body"],
                properties=pika.BasicProperties(
                    headers={
                        "id": headers.get("id"),
                        "shape": json.dumps(answer["shape"]),
                        "ms": json.dumps(ms),
                        "error": error,
                    }
                ),
            )
            channel.basic_ack(method.delivery_tag)
            handled += 1
            if handled % 50 == 0:
                print(f"обработано кадров: {handled}, последний {ms}", flush=True)
    finally:
        try:
            tpu.close()
        finally:
            conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("login")
    parser.add_argument("program")
    parser.add_argument("--threads", type=int, default=3)
    parser.add_argument("--min-conf", type=float, default=0.2)
    args = parser.parse_args(argv)
    stop = threading.Event()
    threads = [
        threading.Thread(
            target=serve, args=(args.login, args.program, args.min_conf, stop), daemon=True
        )
        for _ in range(args.threads)
    ]
    for thread in threads:
        thread.start()
    print(f"сервис кадров: очередь {edge_queue(args.login)}, потоков {args.threads}", flush=True)
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(1)
    except KeyboardInterrupt:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
