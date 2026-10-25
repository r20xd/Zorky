"""Сервис кадров на стенде: разжатие JPEG и сжатие ответа до рамок (без сети и ускорителя)."""

import io
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml"))

from linq import edge_service  # noqa: E402
from worker.detect import decode  # noqa: E402


def fake_output(boxes, nc=5, n=8400, seed=0):
    """Выход YOLO (1, 4+nc, n): шум и несколько настоящих рамок (cx, cy, w, h, класс, оценка)."""
    rng = np.random.default_rng(seed)
    out = rng.random((1, 4 + nc, n), dtype=np.float32) * 0.1
    out[0, :4, :] = rng.random((4, n), dtype=np.float32) * 600 + 20
    for i, (cx, cy, w, h, cls, score) in enumerate(boxes):
        out[0, :4, i] = [cx, cy, w, h]
        out[0, 4:, i] = 0.01
        out[0, 4 + cls, i] = score
    return out


def test_compact_keeps_what_decode_would_keep():
    boxes = [(300, 300, 80, 120, 1, 0.9), (100, 500, 60, 60, 2, 0.8), (500, 100, 50, 50, 0, 0.5)]
    full = fake_output(boxes)
    small = edge_service.compact(full, min_conf=0.2)
    assert small.shape[1] == full.shape[1] and small.shape[2] < 20
    assert small.nbytes < full.nbytes // 100  # сотни байт вместо ~300 КБ
    geometry = (1.0, (0.0, 0.0), (640, 640))
    for conf in (0.3, 0.45):
        want = decode(full, *geometry, conf)
        got = decode(small, *geometry, conf)
        assert len(want[0]) == len(got[0]) == sum(b[5] > conf for b in boxes)
        assert np.allclose(np.sort(want[1]), np.sort(got[1]))


def test_compact_empty_when_nothing_above_threshold():
    out = edge_service.compact(np.full((1, 9, 100), 0.01, np.float32), min_conf=0.2)
    assert out.shape == (1, 9, 0)


def test_jpeg_roundtrip_gives_rgb_tensor_in_range():
    from PIL import Image

    image = Image.new("RGB", (640, 640), (255, 0, 0))  # красный: R=1, G=B=0
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=90)
    tensor = edge_service.jpeg_to_tensor(buf.getvalue())
    assert tensor.shape == (1, 3, 640, 640) and tensor.dtype == np.float32
    assert tensor[0, 0].mean() > 0.9 and tensor[0, 1].mean() < 0.1 and tensor[0, 2].mean() < 0.1


def test_jpeg_of_other_size_is_resized():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (320, 320), (0, 255, 0)).save(buf, "JPEG")
    assert edge_service.jpeg_to_tensor(buf.getvalue()).shape == (1, 3, 640, 640)


def test_edge_queue_name():
    assert edge_service.edge_queue("hackathon1") == "mosprom.edge.hackathon1"
