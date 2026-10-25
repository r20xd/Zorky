"""Слой инференса: загрузка модели и сессии под выбранное устройство (cpu, gpu, linq).

Всё, что зависит от железа, живёт здесь; main.py и export/bench.py берут модель только отсюда.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

DEVICES = ("cpu", "gpu", "linq")
LINQ_ERROR = "нет доступа к LinQ, см. README.md, раздел «LinQ»"


class DeviceUnavailable(RuntimeError):
    """Устройство выбрано, но использовать его сейчас нельзя."""


# API LinQ — пакет pytpu, образец организаторов example_torch_resnet50.py:
#   with tpu.Device.open(tpu.Device.list_devices()[0]) as device:
#     with device.load("x.tpu") as program:
#       with program.inference() as inference:
#         inference.run({имя_входа: float32 NCHW})[имя_узла]  # словари по имени узла
# Device, program и inference - контекстные менеджеры; колёса pytpu в SDK нет (только пример),
# поэтому закрытие сделано через ExitStack и только там, где у объекта есть __enter__/__exit__.
LINQ_MODULE = "pytpu"
# Описание рядом с программой: `<модель>.tpu.json` (пишет export/linq_bench.py):
# {"input": "images", "outputs": ["node_a", ...], "names": {"0": "person"}}
LINQ_META_SUFFIX = ".json"


def load_linq_api():
    """Модуль pytpu; без него — DeviceUnavailable со ссылкой на README."""
    import importlib

    try:
        return importlib.import_module(LINQ_MODULE)
    except ImportError as exc:
        raise DeviceUnavailable(LINQ_ERROR) from exc


class LinqTransient(DeviceUnavailable):
    """Временный сбой пути до LinQ (обрыв, тишина): запрос можно повторить на новом соединении."""


class _Input:
    def __init__(self, name: str):
        self.name = name


class _Meta:
    def __init__(self, data: dict):
        self.custom_metadata_map = data


def linq_description(tpu_path: str) -> dict:
    """Описание программы из `<tpu_path>.json`: имя входа, имена выходов, имена классов."""
    import json
    from pathlib import Path

    meta = Path(str(tpu_path) + LINQ_META_SUFFIX)
    if not meta.is_file():
        raise DeviceUnavailable(f"нет описания {meta}: его пишет export/linq_bench.py")
    data = json.loads(meta.read_text(encoding="utf-8"))
    if not data.get("input") or not data.get("outputs"):
        raise DeviceUnavailable(f"{meta}: нужны поля input и outputs")
    return data


class LinqSession:
    """Сессия LinQ с интерфейсом onnxruntime (get_inputs, get_modelmeta, run):
    OnnxDetector и общая пред/постобработка работают без изменений.
    `path` — скомпилированная программа .tpu."""

    def __init__(self, path: str, api=None):
        api = api or load_linq_api()
        self.description = linq_description(path)
        self.input_name = self.description["input"]
        self.output_names = list(self.description["outputs"])
        devices = api.Device.list_devices()
        if not devices:
            raise DeviceUnavailable("pytpu не видит ни одного устройства LinQ")
        from contextlib import ExitStack

        self._stack = ExitStack()
        try:
            self.device = self._enter(api.Device.open(devices[0]))
            self.program = self._enter(self.device.load(path))
            self.inference = self._enter(self.program.inference())
        except BaseException:
            self._stack.close()
            raise
        names = self.description.get("names")
        self.meta = {"names": str({int(k): v for k, v in names.items()})} if names else {}

    def _enter(self, obj):
        """Как `with obj as x`: результат __enter__ (если менеджера нет - сам объект)."""
        if hasattr(type(obj), "__enter__") and hasattr(type(obj), "__exit__"):
            return self._stack.enter_context(obj)
        return obj

    def close(self):
        """Закрыть inference, программу и устройство (в обратном порядке)."""
        self._stack.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def get_inputs(self):
        return [_Input(self.input_name)]

    def get_modelmeta(self):
        return _Meta(self.meta)

    def run(self, _outputs, feeds: dict):
        """Выходы списком в порядке `outputs` из описания."""
        (tensor,) = feeds.values()
        tensor = np.ascontiguousarray(tensor, dtype=np.float32)
        result = self.inference.run({self.input_name: tensor})
        missing = [n for n in self.output_names if n not in result]
        if missing:
            raise DeviceUnavailable(f"программа не вернула выходы {missing}")
        return [np.asarray(result[n]) for n in self.output_names]


CLOUD_MODULES = ("pika", "tpu_pycloud_format")


def cloud_available() -> bool:
    """Есть ли всё для облачного пути к LinQ (RabbitMQ стенда): pika и tpu_pycloud_format."""
    import importlib.util

    return all(importlib.util.find_spec(name) is not None for name in CLOUD_MODULES)


class CloudSession:
    """LinQ через очередь RabbitMQ стенда (протокол организаторов, ?raw=False, float32).

    Интерфейс onnxruntime, как у LinqSession. Подходит, когда нет `pytpu`: например воркер
    запущен на самом стенде. Через медленный канал VPN (~0,15 МБ/с) кадр 640x640 не пролезет.
    `path` — программа .tpu как её видит стенд (`/auto/cache/<логин>/x.tpu`), рядом `.tpu.json`.
    Настройки — переменные LINQ_HOST, LINQ_PORT, LINQ_BROKER_USER, LINQ_BROKER_PASS,
    LINQ_EXCHANGE, LINQ_INPUT_QUEUE, LINQ_TIMEOUT_S; `modules` и `connection` — подмена в тестах.
    """

    def __init__(self, path: str, modules: dict | None = None, connection=None):
        import os
        import uuid

        env = os.environ.get
        self.description = linq_description(path)
        self.input_name = self.description["input"]
        self.output_names = list(self.description["outputs"])
        self.exchange = env("LINQ_EXCHANGE", "tpu.direct")
        self.input_queue = env("LINQ_INPUT_QUEUE", "input")
        self.timeout = float(env("LINQ_TIMEOUT_S", "60"))
        self.target = f"file://{path}?raw=False"
        try:
            if modules is None:
                import importlib

                modules = {name: importlib.import_module(name) for name in CLOUD_MODULES}
        except ImportError as exc:
            raise DeviceUnavailable(
                f"нет {exc.name}: pip install pika, tpu_pycloud_format"
            ) from exc
        self._fmt = modules["tpu_pycloud_format"]
        self._pika = modules["pika"]
        self.retries = int(env("LINQ_RETRIES", "3"))
        self.reply = f"mosprom_{uuid.uuid4().hex[:8]}"
        self._connection_factory = None if connection is not None else self._new_connection
        self._open(connection if connection is not None else self._new_connection())
        self._messages = None
        names = self.description.get("names")
        self.meta = {"names": str({int(k): v for k, v in names.items()})} if names else {}
        #: сколько времени заняли последние запросы, мс (для метрик воркера)
        self.last_ms = 0.0

    def _new_connection(self):
        import os

        env = os.environ.get
        params = self._pika.ConnectionParameters(
            env("LINQ_HOST", "10.0.114.30"),
            int(env("LINQ_PORT", "5672")),
            credentials=self._pika.PlainCredentials(
                env("LINQ_BROKER_USER", "guest"), env("LINQ_BROKER_PASS", "guest")
            ),
            heartbeat=int(env("LINQ_HEARTBEAT_S", "30")),
            blocked_connection_timeout=30,
        )
        return self._pika.BlockingConnection(params)

    def _open(self, connection):
        self.connection = connection
        self.channel = connection.channel()
        # не auto_delete: он удаляет очередь, как только снят consumer
        self.channel.queue_declare(queue=self.reply, auto_delete=False)
        self.channel.queue_bind(exchange=self.exchange, queue=self.reply, routing_key=self.reply)
        self._messages = None

    def _reconnect(self):
        """Новое соединение и своя очередь ответов; старое закрываем, ошибки закрытия не важны."""
        try:
            self.connection.close()
        except Exception:  # noqa: BLE001 - соединение уже мертво
            pass
        if self._connection_factory is None:  # соединение подставили снаружи (тест): то же
            self._open(self.connection)
        else:
            self._open(self._connection_factory())

    def close(self):
        try:
            self.channel.queue_delete(queue=self.reply)
        finally:
            self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def get_inputs(self):
        return [_Input(self.input_name)]

    def get_modelmeta(self):
        return _Meta(self.meta)

    def _receive(self, task_id: str):
        import time

        if self._messages is None:  # один постоянный consumer: отмена теряет уже доставленное
            self._messages = self.channel.consume(self.reply, auto_ack=True, inactivity_timeout=1.0)
        deadline = time.monotonic() + self.timeout
        for method, props, body in self._messages:
            if method is None:
                if time.monotonic() > deadline:
                    raise LinqTransient(f"LinQ не ответил за {self.timeout:.0f} с")
                continue
            if props.headers.get(self._fmt.MESSAGE_ID_ATTR_NAME) != task_id:
                continue  # запоздалый ответ на прошлый запрос
            data, _meta, worker = self._fmt.deserialize(body)
            if not data:
                raise DeviceUnavailable(f"ошибка воркера LinQ: {worker.get('error_message')}")
            return data
        raise LinqTransient("очередь ответов LinQ закрыта")

    def _run_once(self, tensor):
        """Выходы списком в порядке `outputs` из описания."""
        import json
        import time
        import uuid

        task_id = uuid.uuid4().hex
        started = time.monotonic()
        self.channel.basic_publish(
            exchange=self.exchange,
            routing_key=self.input_queue,
            body=self._fmt.serialize(self._fmt.PyTask({self.input_name: tensor})),
            properties=self._pika.BasicProperties(
                headers={
                    self._fmt.TARGET_URI_ATTR_NAME: self.target,
                    self._fmt.RESPONSE_ROUTING_KEYS_ATTR_NAME: json.dumps([self.reply]),
                    self._fmt.MESSAGE_ID_ATTR_NAME: task_id,
                }
            ),
        )
        data = self._receive(task_id)
        self.last_ms = (time.monotonic() - started) * 1000
        missing = [n for n in self.output_names if n not in data]
        if missing:
            raise DeviceUnavailable(f"программа не вернула выходы {missing}")
        return [np.asarray(data[n]) for n in self.output_names]

    def run(self, _outputs, feeds: dict):
        """Выходы списком в порядке `outputs`; обрыв или тишина — новое соединение и повтор."""
        import time

        (tensor,) = feeds.values()
        tensor = np.ascontiguousarray(tensor, dtype=np.float32)
        transient = (LinqTransient, OSError, *self._amqp_errors())
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._run_once(tensor)
            except transient as exc:
                last = exc
                if attempt == self.retries:
                    break
                time.sleep(min(2.0 * (attempt + 1), 10.0))
                try:
                    self._reconnect()
                except transient as reconnect_exc:
                    last = reconnect_exc
        raise DeviceUnavailable(f"LinQ недоступен после {self.retries + 1} попыток: {last}")

    def _amqp_errors(self) -> tuple:
        exceptions = getattr(self._pika, "exceptions", None)
        base = getattr(exceptions, "AMQPError", None)
        return (base,) if isinstance(base, type) else ()


def edge_mode() -> bool:
    """LINQ_MODE=edge: кадры уходят на стенд сервису linq/edge_service.py (JPEG вместо тензора)."""
    import os

    return os.environ.get("LINQ_MODE", "") == "edge"


class EdgeSession(CloudSession):
    """LinQ через сервис на стенде: по VPN идёт сжатый кадр, а не тензор 4,9 МБ.

    Интерфейс onnxruntime, как у CloudSession: `OnnxDetector` и `decode` работают без изменений,
    потому что сервис возвращает рамки в том же формате выхода YOLO, только короче.
    Настройки: LINQ_EDGE_QUEUE (очередь сервиса, `mosprom.edge.<логин>`), LINQ_EDGE_QUALITY
    (качество JPEG, 75), LINQ_EDGE_SIZE (сторона кадра при отправке, 640; меньше - быстрее по
    медленному каналу, но хуже мелкие объекты).
    """

    def __init__(self, path: str, modules: dict | None = None, connection=None):
        import os

        env = os.environ.get
        if modules is None:
            try:
                import importlib

                modules = {"pika": importlib.import_module("pika"), "tpu_pycloud_format": None}
            except ImportError as exc:
                raise DeviceUnavailable("нет pika: pip install pika") from exc
        super().__init__(path, modules=modules, connection=connection)
        self.edge_queue = env("LINQ_EDGE_QUEUE", "")
        if not self.edge_queue:
            raise DeviceUnavailable("задайте LINQ_EDGE_QUEUE (очередь сервиса на стенде)")
        self.quality = int(env("LINQ_EDGE_QUALITY", "75"))
        self.send_size = int(env("LINQ_EDGE_SIZE", "640"))
        self._last: tuple[bytes, np.ndarray] | None = None
        #: разбор последнего ответа сервиса: время разжатия, ускорителя и всего на стенде, мс
        self.last_stats: dict = {}
        self.sent_bytes = 0

    def _encode(self, tensor: np.ndarray) -> bytes:
        import cv2

        image = np.ascontiguousarray((tensor[0].transpose(1, 2, 0) * 255).round().astype(np.uint8))
        if self.send_size != image.shape[0]:
            image = cv2.resize(image, (self.send_size, self.send_size))
        ok, buf = cv2.imencode(".jpg", image[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        if not ok:
            raise DeviceUnavailable("не удалось сжать кадр")
        return buf.tobytes()

    def _receive_edge(self, task_id: str) -> tuple[np.ndarray, dict]:
        import json
        import time

        if self._messages is None:
            self._messages = self.channel.consume(self.reply, auto_ack=True, inactivity_timeout=1.0)
        deadline = time.monotonic() + self.timeout
        for method, props, body in self._messages:
            if method is None:
                if time.monotonic() > deadline:
                    raise LinqTransient(f"сервис кадров не ответил за {self.timeout:.0f} с")
                continue
            headers = props.headers or {}
            if headers.get("id") != task_id:
                continue  # запоздалый ответ на прошлый кадр
            if headers.get("error"):
                raise DeviceUnavailable(f"ошибка сервиса кадров: {headers['error']}")
            shape = json.loads(headers.get("shape", "[1,0,0]"))
            array = np.frombuffer(body, dtype=np.float32).reshape(shape)
            return array.copy(), json.loads(headers.get("ms", "{}"))
        raise LinqTransient("очередь ответов закрыта")

    def _run_once(self, tensor):
        import hashlib
        import time
        import uuid

        digest = hashlib.blake2b(tensor.tobytes(), digest_size=16).digest()
        if self._last is not None and self._last[0] == digest:
            return [self._last[1]]  # тот же кадр спрашивают второй раз (люди и СИЗ): ответ готов
        jpeg = self._encode(tensor)
        task_id = uuid.uuid4().hex
        started = time.monotonic()
        self.channel.basic_publish(
            exchange="",
            routing_key=self.edge_queue,
            body=jpeg,
            properties=self._pika.BasicProperties(headers={"id": task_id, "reply": self.reply}),
        )
        output, self.last_stats = self._receive_edge(task_id)
        self.last_ms = (time.monotonic() - started) * 1000
        self.sent_bytes = len(jpeg)
        self._last = (digest, output)
        return [output]


def check_device(device: str) -> None:
    if device not in DEVICES:
        raise ValueError(f"неизвестное устройство {device!r}, нужно одно из {DEVICES}")
    if device == "linq" and edge_mode():
        import importlib.util

        if importlib.util.find_spec("pika") is None:
            raise DeviceUnavailable("нет pika: pip install pika")
    elif device == "linq" and not cloud_available():
        load_linq_api()  # нет ни pytpu, ни облачного пути: понятная ошибка про LinQ


def _yolo(path: str, **kwargs):
    from ultralytics import YOLO

    return YOLO(path, **kwargs)


def load_model(path: str, device: str = "cpu", factory: Callable[..., object] | None = None):
    """YOLO из .pt или .onnx; для .onnx нужен onnxruntime. `factory` — подмена в тестах."""
    check_device(device)
    if device == "linq":
        raise DeviceUnavailable(
            "на LinQ работают только .onnx: --model x.onnx, см. README.md, раздел «LinQ»"
        )
    make = factory or _yolo
    if str(path).lower().endswith(".onnx"):
        return make(path, task="detect")
    model = make(path)
    if device == "gpu":
        model.to("cuda")
    return model


def make_session(
    onnx_path: str, device: str = "cpu", factory: Callable[..., object] | None = None, linq_api=None
):
    """Сессия onnxruntime (для linq — LinqSession). `factory`, `linq_api` — подмена в тестах."""
    if device not in DEVICES:
        check_device(device)
    if device == "linq" and edge_mode():
        return EdgeSession(onnx_path)
    if device == "linq":
        try:
            return LinqSession(onnx_path, api=linq_api)
        except DeviceUnavailable:
            if linq_api is not None or not cloud_available():
                raise
            return CloudSession(onnx_path)  # pytpu нет, но есть путь через RabbitMQ стенда
    if factory is None:
        import onnxruntime as ort

        factory = ort.InferenceSession
    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if device == "gpu" else None
    providers = providers or ["CPUExecutionProvider"]
    return factory(onnx_path, providers=providers)
