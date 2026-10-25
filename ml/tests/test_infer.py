import json
import sys

import numpy as np
import pytest

from worker.detect import OnnxDetector
from worker.infer import (
    LINQ_ERROR,
    LINQ_MODULE,
    DeviceUnavailable,
    LinqSession,
    check_device,
    load_model,
    make_session,
)


class FakeModel:
    def __init__(self, path, **kwargs):
        self.path, self.kwargs, self.moved = path, kwargs, None

    def to(self, device):
        self.moved = device


def test_cpu_pt():
    model = load_model("a.pt", "cpu", factory=FakeModel)
    assert model.path == "a.pt" and model.kwargs == {} and model.moved is None


def test_gpu_moves_to_cuda():
    assert load_model("a.pt", "gpu", factory=FakeModel).moved == "cuda"


def test_onnx_task_detect():
    assert load_model("a.onnx", factory=FakeModel).kwargs == {"task": "detect"}


def test_linq_without_sdk(monkeypatch):
    monkeypatch.setattr("worker.infer.cloud_available", lambda: False)
    with pytest.raises(DeviceUnavailable, match="README.md, раздел «LinQ»"):
        make_session("a.onnx", "linq")
    with pytest.raises(DeviceUnavailable, match=LINQ_ERROR):
        check_device("linq")
    with pytest.raises(DeviceUnavailable, match="README"):
        load_model("a.pt", "linq", factory=FakeModel)


class FakePytpu:
    """Подмена pytpu: Device.list_devices/open -> load -> inference -> run."""

    def __init__(self):
        self.calls = []
        self.Device = self

    def list_devices(self):
        self.calls.append("list")
        return ["tpu0"]

    def open(self, name):
        self.calls.append(("open", name))
        return self

    def load(self, path):
        self.calls.append(("load", path))
        return self

    def inference(self):
        return self

    def run(self, feeds):
        self.calls.append(("run", {k: (v.dtype, v.shape) for k, v in feeds.items()}))
        return {"head": np.zeros((1, 84, 8400), dtype=np.float32), "extra": np.ones(1)}


def test_linq_session_through_fake_api(monkeypatch, tmp_path):
    tpu = tmp_path / "a.tpu"
    tpu.write_bytes(b"")
    (tmp_path / "a.tpu.json").write_text(
        json.dumps({"input": "in0", "outputs": ["head"], "names": {"0": "person"}})
    )
    api = FakePytpu()
    session = make_session(str(tpu), "linq", linq_api=api)
    assert api.calls[:3] == ["list", ("open", "tpu0"), ("load", str(tpu))]
    assert session.get_inputs()[0].name == "in0"
    out = session.run(None, {"in0": np.zeros((1, 3, 640, 640), np.float64)})
    assert api.calls[-1] == ("run", {"in0": (np.dtype("float32"), (1, 3, 640, 640))})
    assert len(out) == 1 and out[0].shape == (1, 84, 8400)
    detector = OnnxDetector(session)
    assert detector.names == {0: "person"}
    boxes, scores, classes = detector(np.zeros((480, 640, 3), np.uint8), 0.25)
    assert len(boxes) == 0
    monkeypatch.setitem(sys.modules, LINQ_MODULE, api)
    check_device("linq")


def test_linq_session_closes_context_managers(tmp_path):
    log = []

    class CM:
        def __init__(self, name, child=None):
            self.name, self.child = name, child

        def __enter__(self):
            log.append(f"enter {self.name}")
            return self

        def __exit__(self, *exc):
            log.append(f"exit {self.name}")

        def load(self, path):
            return CM("program", self.child)

        def inference(self):
            return CM("inference")

        def run(self, feeds):
            return {"head": np.zeros(1)}

    class Api:
        class Device:
            list_devices = staticmethod(lambda: ["tpu0"])
            open = staticmethod(lambda name: CM("device"))

    tpu = tmp_path / "a.tpu"
    (tmp_path / "a.tpu.json").write_text(json.dumps({"input": "in0", "outputs": ["head"]}))
    with LinqSession(str(tpu), api=Api) as session:
        session.run(None, {"in0": np.zeros((1, 3, 4, 4))})
    assert log == [
        "enter device",
        "enter program",
        "enter inference",
        "exit inference",
        "exit program",
        "exit device",
    ]


def test_linq_without_description(tmp_path):
    with pytest.raises(DeviceUnavailable, match="linq_bench"):
        make_session(str(tmp_path / "a.tpu"), "linq", linq_api=FakePytpu())


def test_unknown_device():
    with pytest.raises(ValueError):
        load_model("a.pt", "tpu", factory=FakeModel)


def test_session_providers():
    session = make_session("a.onnx", "cpu", factory=lambda p, providers: (p, providers))
    assert session == ("a.onnx", ["CPUExecutionProvider"])


class FakeFmt:
    """Модуль tpu_pycloud_format: сериализация — словарь как есть."""

    MESSAGE_ID_ATTR_NAME = "id"
    RESPONSE_ROUTING_KEYS_ATTR_NAME = "reply"
    TARGET_URI_ATTR_NAME = "target"

    class PyTask(dict):
        pass

    @staticmethod
    def serialize(task):
        return dict(task)

    @staticmethod
    def deserialize(body):
        return body["data"], {}, {"error_message": body.get("error")}


class FakeChannel:
    def __init__(self, outputs, error=None):
        self.published, self.outputs, self.error = [], outputs, error
        self.queue_deleted = False
        self._pending = []

    def queue_declare(self, **kwargs):
        self.declared = kwargs

    def queue_bind(self, **kwargs):
        self.bound = kwargs

    def basic_publish(self, exchange, routing_key, body, properties):
        self.published.append((exchange, routing_key, body, properties.headers))
        data = {} if self.error else self.outputs
        reply = {"data": data, "error": self.error}
        self._pending.append((properties.headers["id"], reply))

    def consume(self, queue, auto_ack, inactivity_timeout):
        yield None, None, None  # тишина, затем ответ
        while self._pending:
            task_id, reply = self._pending.pop(0)

            class P:
                headers = {"id": "чужой"}

            yield object(), P, {"data": {"zz": 1}}  # чужой ответ пропускается
            P2 = type("P2", (), {"headers": {"id": task_id}})
            yield object(), P2, reply

    def queue_delete(self, queue):
        self.queue_deleted = True


class FakeConn:
    def __init__(self, channel):
        self._channel, self.closed = channel, False

    def channel(self):
        return self._channel

    def close(self):
        self.closed = True


class FakePika:
    class BasicProperties:
        def __init__(self, headers):
            self.headers = headers


def make_cloud(tmp_path, channel):
    from worker.infer import CloudSession

    tpu = tmp_path / "a.tpu"
    tpu.write_bytes(b"")
    (tmp_path / "a.tpu.json").write_text(
        json.dumps({"input": "images", "outputs": ["h0", "h1"], "names": {"0": "person"}})
    )
    modules = {"pika": FakePika, "tpu_pycloud_format": FakeFmt}
    return CloudSession(str(tpu), modules=modules, connection=FakeConn(channel)), tpu


def test_cloud_session_roundtrip(tmp_path):
    outputs = {"h0": np.zeros((1, 84, 8400), np.float32), "h1": np.ones((1, 3), np.float32)}
    channel = FakeChannel(outputs)
    session, tpu = make_cloud(tmp_path, channel)
    assert session.get_inputs()[0].name == "images"
    out = session.run(None, {"x": np.zeros((1, 3, 640, 640), np.float64)})
    assert [o.shape for o in out] == [(1, 84, 8400), (1, 3)]
    exchange, key, body, headers = channel.published[0]
    assert (exchange, key) == ("tpu.direct", "input")
    assert headers["target"] == f"file://{tpu}?raw=False"
    assert body["images"].dtype == np.float32
    assert json.loads(headers["reply"]) == [session.reply]
    assert channel.declared["auto_delete"] is False
    assert session.last_ms >= 0
    detector = OnnxDetector(session)
    assert detector.names == {0: "person"}
    session.close()
    assert channel.queue_deleted and session.connection.closed


def test_cloud_session_worker_error(tmp_path):
    session, _ = make_cloud(tmp_path, FakeChannel({}, error="нет программы"))
    with pytest.raises(DeviceUnavailable, match="нет программы"):
        session.run(None, {"x": np.zeros((1, 3, 8, 8))})


def test_cloud_session_missing_output(tmp_path):
    session, _ = make_cloud(tmp_path, FakeChannel({"h0": np.zeros(1)}))
    with pytest.raises(DeviceUnavailable, match="h1"):
        session.run(None, {"x": np.zeros((1, 3, 8, 8))})


def test_linq_falls_back_to_cloud(monkeypatch, tmp_path):
    monkeypatch.setattr("worker.infer.cloud_available", lambda: True)
    created = []
    monkeypatch.setattr("worker.infer.CloudSession", lambda path: created.append(path) or "cloud")
    assert make_session(str(tmp_path / "a.tpu"), "linq") == "cloud"
    assert created == [str(tmp_path / "a.tpu")]
    # явно переданный pytpu не подменяется облаком
    with pytest.raises(DeviceUnavailable):
        make_session(str(tmp_path / "a.tpu"), "linq", linq_api=FakePytpu())


class FlakyChannel(FakeChannel):
    """Первые `fail` публикаций рвутся, как при обрыве VPN."""

    def __init__(self, outputs, fail):
        super().__init__(outputs)
        self.fail = fail

    def basic_publish(self, *args, **kwargs):
        if self.fail:
            self.fail -= 1
            raise OSError("соединение разорвано")
        super().basic_publish(*args, **kwargs)


def test_cloud_session_retries_after_connection_loss(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    outputs = {"h0": np.zeros((1, 9, 4), np.float32), "h1": np.ones((1, 3), np.float32)}
    channel = FlakyChannel(outputs, fail=2)
    session, _ = make_cloud(tmp_path, channel)
    out = session.run(None, {"x": np.zeros((1, 3, 8, 8))})
    assert [o.shape for o in out] == [(1, 9, 4), (1, 3)]
    assert channel.fail == 0


def test_cloud_session_gives_up_after_retries(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    monkeypatch.setenv("LINQ_RETRIES", "1")
    session, _ = make_cloud(tmp_path, FlakyChannel({}, fail=10))
    with pytest.raises(DeviceUnavailable, match="после 2 попыток"):
        session.run(None, {"x": np.zeros((1, 3, 8, 8))})


def test_cloud_session_does_not_retry_worker_error(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr("time.sleep", sleeps.append)
    session, _ = make_cloud(tmp_path, FakeChannel({}, error="нет программы"))
    with pytest.raises(DeviceUnavailable, match="нет программы"):
        session.run(None, {"x": np.zeros((1, 3, 8, 8))})
    assert sleeps == []  # ошибка программы не лечится повтором


def test_make_detectors_accepts_tpu_program_only_on_linq(monkeypatch, tmp_path):
    import argparse

    from worker import main as worker_main

    made = []
    monkeypatch.setattr(
        "worker.infer.make_session", lambda path, device: made.append((path, device)) or "s"
    )
    monkeypatch.setattr(worker_main, "OnnxPeople", lambda session: ("people", session))
    monkeypatch.setattr(worker_main, "OnnxPpe", lambda session: ("ppe", session))
    args = argparse.Namespace(model="a.tpu", ppe_weights="a.tpu", device="linq", pose_model=None)
    people, ppe = worker_main.make_detectors(args, {})
    assert people == ("people", "s") and ppe == ("ppe", "s")
    assert made == [("a.tpu", "linq")]  # одна сессия на программу, не две
    args.device = "cpu"
    monkeypatch.setattr(worker_main, "load_model", lambda path, device: ("pt", path))
    people, _ = worker_main.make_detectors(args, {})
    assert callable(people)  # на CPU .tpu не читается своим слоем: идёт как .pt-модель


class EdgeChannel(FakeChannel):
    """Сервис кадров: на каждый JPEG отвечает рамками (сырым телом) и заголовками."""

    def __init__(self, output, error=""):
        super().__init__({})
        self.array, self.error = output, error
        self.jpegs = []
        self.queue = None

    def basic_publish(self, exchange, routing_key, body, properties):
        self.jpegs.append(body)
        self.queue = (exchange, routing_key)
        self._pending.append((properties.headers["id"], properties.headers["reply"]))

    def consume(self, queue, auto_ack, inactivity_timeout):
        yield None, None, None
        while self._pending:
            task_id, _reply = self._pending.pop(0)
            props = type(
                "P",
                (),
                {
                    "headers": {
                        "id": task_id,
                        "shape": json.dumps(list(self.array.shape)),
                        "ms": json.dumps({"tpu": 150.0, "total": 180.0}),
                        "error": self.error,
                    }
                },
            )
            yield object(), props, self.array.tobytes()


def make_edge(tmp_path, channel, monkeypatch):
    from worker.infer import EdgeSession

    monkeypatch.setenv("LINQ_EDGE_QUEUE", "mosprom.edge.login")
    tpu = tmp_path / "a.tpu"
    tpu.write_bytes(b"")
    (tmp_path / "a.tpu.json").write_text(
        json.dumps({"input": "images", "outputs": ["h0"], "names": {"0": "person", "1": "helmet"}})
    )
    modules = {"pika": FakePika, "tpu_pycloud_format": None}
    return EdgeSession(str(tpu), modules=modules, connection=FakeConn(channel))


def test_edge_session_sends_small_jpeg_and_returns_boxes(tmp_path, monkeypatch):
    boxes = np.zeros((1, 6, 2), np.float32)
    boxes[0, :, 0] = [320, 320, 100, 200, 0.9, 0.1]
    channel = EdgeChannel(boxes)
    session = make_edge(tmp_path, channel, monkeypatch)
    tensor = np.random.default_rng(0).random((1, 3, 640, 640), dtype=np.float32)
    (out,) = session.run(None, {"images": tensor})
    assert out.shape == (1, 6, 2) and out[0, 4, 0] == np.float32(0.9)
    assert channel.queue == ("", "mosprom.edge.login")
    assert len(channel.jpegs[0]) < tensor.nbytes // 5  # JPEG, а не тензор
    assert channel.jpegs[0][:2] == b"\xff\xd8"
    assert session.last_stats["tpu"] == 150.0 and session.sent_bytes == len(channel.jpegs[0])
    assert OnnxDetector(session).names == {0: "person", 1: "helmet"}


def test_edge_session_reuses_answer_for_the_same_frame(tmp_path, monkeypatch):
    """Люди и СИЗ - один и тот же кадр и одна программа: на стенд уходит один запрос."""
    channel = EdgeChannel(np.zeros((1, 6, 1), np.float32))
    session = make_edge(tmp_path, channel, monkeypatch)
    tensor = np.full((1, 3, 640, 640), 0.5, np.float32)
    session.run(None, {"images": tensor})
    session.run(None, {"images": tensor})
    assert len(channel.jpegs) == 1
    session.run(None, {"images": tensor * 0.5})
    assert len(channel.jpegs) == 2


def test_edge_session_service_error_is_not_retried(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr("time.sleep", sleeps.append)
    session = make_edge(
        tmp_path, EdgeChannel(np.zeros((1, 0, 0), np.float32), "нет программы"), monkeypatch
    )
    with pytest.raises(DeviceUnavailable, match="нет программы"):
        session.run(None, {"images": np.zeros((1, 3, 640, 640), np.float32)})
    assert sleeps == []


def test_edge_mode_needs_queue_name(tmp_path, monkeypatch):
    from worker.infer import EdgeSession

    monkeypatch.delenv("LINQ_EDGE_QUEUE", raising=False)
    tpu = tmp_path / "a.tpu"
    (tmp_path / "a.tpu.json").write_text(json.dumps({"input": "i", "outputs": ["o"]}))
    with pytest.raises(DeviceUnavailable, match="LINQ_EDGE_QUEUE"):
        EdgeSession(
            str(tpu),
            modules={"pika": FakePika, "tpu_pycloud_format": None},
            connection=FakeConn(FakeChannel({})),
        )


def test_linq_runs_tpu_program_for_ppe_and_cpu_for_plain_onnx(monkeypatch):
    """Людей считает обычный .onnx (CPU), СИЗ - программа .tpu на ускорителе."""
    import argparse

    from worker import main as worker_main

    made = []
    monkeypatch.setattr(
        "worker.infer.make_session", lambda path, device: made.append((path, device)) or path
    )
    monkeypatch.setattr(worker_main, "OnnxPeople", lambda session: ("people", session))
    monkeypatch.setattr(worker_main, "OnnxPpe", lambda session: ("ppe", session))
    args = argparse.Namespace(
        model="people.onnx", ppe_weights="ppe.tpu", device="linq", pose_model=None
    )
    worker_main.make_detectors(args, {})
    assert made == [("people.onnx", "cpu"), ("ppe.tpu", "linq")]
