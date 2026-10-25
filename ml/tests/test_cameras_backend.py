import threading

from worker.supervisor import CameraSupervisor, mask_source, stoppable

PASSWORD = "s3cret"
URL1 = f"rtsp://admin:{PASSWORD}@10.0.0.1/stream"
URL2 = f"rtsp://admin:{PASSWORD}@10.0.0.2/stream"


class Backend:
    def __init__(self):
        self.cameras = []
        self.down = False

    def get_cameras(self):
        if self.down:
            raise OSError(f"нет связи с {URL1}")
        return self.cameras


def make(backend):
    started = []

    def start(camera_id, source, stop):
        started.append((camera_id, source, stop))
        thread = threading.Thread(target=stop.wait, daemon=True)
        thread.start()
        return thread

    return CameraSupervisor(backend, start), started


def test_mask():
    assert mask_source(URL1) == "rtsp://***@10.0.0.1/stream"
    assert mask_source("rtsp://h/p?token=abc") == "rtsp://h/p?***"


def test_stoppable():
    stop = threading.Event()
    got = []
    for item in stoppable(iter(range(5)), stop):
        got.append(item)
        if item == 1:
            stop.set()
    assert got == [0, 1]


def test_lifecycle_and_no_password_in_log(capsys):
    backend = Backend()
    sup, started = make(backend)
    backend.cameras = [{"id": "a", "source": URL1}, {"id": "b", "name": "без source"}]
    sup.sync()
    assert [s[:2] for s in started] == [("a", URL1)]
    sup.sync()  # без изменений
    assert len(started) == 1
    backend.cameras.append({"id": "c", "source": URL2})
    sup.sync()
    assert [s[0] for s in started] == ["a", "c"]
    backend.cameras = [{"id": "a", "source": URL2}]  # a сменила адрес, c удалена
    sup.sync()
    assert [s[:2] for s in started[2:]] == [("a", URL2)]
    assert started[0][2].is_set() and started[1][2].is_set() and not started[2][2].is_set()
    assert set(sup.running) == {"a"}
    backend.down = True  # бэкенд недоступен: работает по последнему списку
    sup.sync()
    assert len(started) == 3 and not started[2][2].is_set()
    sup.stop_all()
    err = capsys.readouterr().err
    assert PASSWORD not in err and "нет source" in err and "недоступен" in err


def test_start_failure_masked(capsys):
    backend = Backend()
    backend.cameras = [{"id": "a", "source": URL1}]

    def start(camera_id, source, stop):
        raise RuntimeError(f"не открыть {source}")

    sup = CameraSupervisor(backend, start)
    sup.sync()
    assert PASSWORD not in capsys.readouterr().err
    assert not sup.running
