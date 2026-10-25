import urllib.error

from worker.main import read_frames, with_retries


class FakeCapture:
    def __init__(self, frames):
        self.frames = list(frames)
        self.released = False

    def read(self):
        return (True, self.frames.pop(0)) if self.frames else (False, None)

    def release(self):
        self.released = True


def opener(*batches):
    queue = [FakeCapture(b) for b in batches]
    return lambda: queue.pop(0), queue


def test_file_ends_without_loop():
    open_capture, _ = opener([1, 2])
    assert list(read_frames(open_capture, "a.mp4", sleep=lambda s: None)) == [1, 2]


def test_file_loops():
    open_capture, _ = opener([1, 2], [1, 2], [1, 2])
    gen = read_frames(open_capture, "a.mp4", loop=True, sleep=lambda s: None)
    assert [next(gen) for _ in range(5)] == [1, 2, 1, 2, 1]


def test_rtsp_reconnects_after_drop():
    open_capture, _ = opener([1], [], [2])
    sleeps = []
    gen = read_frames(open_capture, "rtsp://cam/1", sleep=sleeps.append)
    assert [next(gen), next(gen)] == [1, 2]
    assert len(sleeps) == 2


def test_retries_then_succeeds():
    calls = []

    def call():
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.URLError("down")
        return "ok"

    assert with_retries(call, "x", sleep=lambda s: None) == "ok"


def test_backend_down_does_not_raise():
    def call():
        raise urllib.error.URLError("down")

    assert with_retries(call, "x", attempts=2, sleep=lambda s: None) is None


def test_open_source_webcam_number_file_and_url():
    from worker.main import is_stream, open_source

    class Cv2:
        def __init__(self):
            self.args = []

        def VideoCapture(self, arg):  # noqa: N802
            self.args.append(arg)

    cv2 = Cv2()
    for source in ("0", "cam.mp4", "rtsp://host/1", "http://10.0.0.5:8080/video"):
        open_source(cv2, source)
    assert cv2.args == [0, "cam.mp4", "rtsp://host/1", "http://10.0.0.5:8080/video"]
    assert isinstance(cv2.args[0], int)
    assert is_stream("0") and not is_stream("cam.mp4")
