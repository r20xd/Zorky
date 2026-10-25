"""Одно событие на трек: последовательности кадров с подменой модели трекера."""

from types import SimpleNamespace

from worker.config import CLEAR_S, DEBOUNCE_MIN_FRAMES, DEBOUNCE_WINDOW_S
from worker.main import yolo_detections
from worker.pipeline import find_violations
from worker.rules import Debouncer

ZONES = [
    {
        "id": "z-1",
        "kind": "danger",
        "polygon": [[0.5, 0.3], [1.0, 0.3], [1.0, 1.0], [0.5, 1.0]],
    }
]


class Tensor(list):
    def int(self):
        return self

    def tolist(self):
        return list(self)


class FakeModel:
    """Вместо YOLO: на каждом вызове track отдаёт очередной кадр (id, рамка в пикселях 100x100)."""

    def __init__(self, frames):
        self.frames = iter(frames)

    def track(self, *args, **kwargs):
        rows = next(self.frames)
        boxes = SimpleNamespace(
            xyxy=Tensor([r[1] for r in rows]),
            conf=Tensor([0.9] * len(rows)),
            cls=Tensor([0] * len(rows)),
            id=Tensor([r[0] for r in rows]) if rows else None,
        )
        return [SimpleNamespace(boxes=boxes)]


def run(frames, debouncer):
    model = FakeModel(frames)
    fired = []
    for t in range(len(frames)):
        dets = yolo_detections(model, SimpleNamespace(shape=(100, 100, 3)))
        fired += find_violations(dets, ZONES, debouncer, now=t / 10)
    return fired


IN_ZONE = [60, 40, 80, 90]


def test_one_event_per_track_without_repeats():
    fired = run([[(1, IN_ZONE)]] * 50, Debouncer(min_frames=3))
    assert [v.detection.track_id for v in fired] == [1]


def test_lost_frames_and_return_same_track_no_second_event():
    frames = [[(1, IN_ZONE)]] * 5 + [[]] * 3 + [[(1, IN_ZONE)]] * 5
    assert len(run(frames, Debouncer(min_frames=3))) == 1


def test_two_tracks_give_two_events():
    frames = [[(1, IN_ZONE), (2, IN_ZONE)]] * 6
    assert sorted(v.detection.track_id for v in run(frames, Debouncer(min_frames=3))) == [1, 2]


def test_track_per_episode_not_blocked_by_other_track():
    # Трек 2 появляется во время эпизода трека 1 — его событие не теряется
    frames = [[(1, IN_ZONE)]] * 4 + [[(1, IN_ZONE), (2, IN_ZONE)]] * 4
    fired = run(frames, Debouncer(min_frames=3))
    assert [v.detection.track_id for v in fired] == [1, 2]


def test_blink_and_short_track_loss_give_no_duplicate():
    # кадры по 0.1 с: мигание детекции и потеря трека на 1-2 кадра внутри эпизода
    frames = [[(1, IN_ZONE)]] * 4 + [[]] + [[(1, IN_ZONE)]] * 3 + [[], []] + [[(1, IN_ZONE)]] * 10
    assert len(run(frames, Debouncer(min_frames=3))) == 1


def test_new_episode_after_violation_gone_for_clear_s():
    # 0.1 с на кадр: 40 кадров (4 с) без трека > clear_s=3 -> новый эпизод
    frames = [[(1, IN_ZONE)]] * 5 + [[]] * 40 + [[(1, IN_ZONE)]] * 5
    assert len(run(frames, Debouncer(min_frames=3))) == 2


def test_default_params_come_from_config():
    d = Debouncer()
    assert (d.min_frames, d.window_s, d.clear_s) == (
        DEBOUNCE_MIN_FRAMES,
        DEBOUNCE_WINDOW_S,
        CLEAR_S,
    )
