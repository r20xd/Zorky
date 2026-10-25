"""Своя пред- и постобработка YOLO на numpy: letterbox, разбор выхода, NMS. Без ultralytics.

Выход ONNX-модели YOLO (v8/11): (1, 4+nc, N), первые 4 строки — центр, ширина, высота
в пикселях letterbox-кадра, дальше оценки классов.
"""

from __future__ import annotations

import ast

import numpy as np

PAD_VALUE = 114


def letterbox(frame: np.ndarray, size: int = 640) -> tuple[np.ndarray, float, tuple[float, float]]:
    """BGR-кадр -> (тензор 1x3xSxS float32 RGB 0..1, масштаб, сдвиг (dx, dy))."""
    import cv2

    height, width = frame.shape[:2]
    scale = min(size / height, size / width)
    new_w, new_h = round(width * scale), round(height * scale)
    resized = frame if (new_w, new_h) == (width, height) else cv2.resize(frame, (new_w, new_h))
    dx, dy = (size - new_w) / 2, (size - new_h) / 2
    top, left = round(dy - 0.1), round(dx - 0.1)
    canvas = np.full((size, size, 3), PAD_VALUE, dtype=np.uint8)
    canvas[top : top + new_h, left : left + new_w] = resized
    tensor = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    return np.ascontiguousarray(tensor), scale, (float(left), float(top))


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """IoU всех пар рамок xyxy: (len(a), len(b))."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    lt = np.maximum(a[:, None, :2], b[None, :, :2])
    rb = np.minimum(a[:, None, 2:], b[None, :, 2:])
    inter = np.prod(np.clip(rb - lt, 0, None), axis=2)
    area_a = np.prod(a[:, 2:] - a[:, :2], axis=1)
    area_b = np.prod(b[:, 2:] - b[:, :2], axis=1)
    return inter / np.maximum(area_a[:, None] + area_b[None, :] - inter, 1e-9)


def nms(boxes: np.ndarray, scores: np.ndarray, classes: np.ndarray, iou: float) -> list[int]:
    """NMS по классам; индексы оставленных рамок по убыванию оценки."""
    order = np.argsort(-scores, kind="stable")
    keep: list[int] = []
    while len(order):
        i = order[0]
        keep.append(int(i))
        rest = order[1:]
        if not len(rest):
            break
        overlap = iou_matrix(boxes[i : i + 1], boxes[rest])[0]
        order = rest[~((overlap > iou) & (classes[rest] == classes[i]))]
    return keep


def decode(
    output: np.ndarray,
    scale: float,
    pad: tuple[float, float],
    shape: tuple[int, int],
    conf: float,
    iou: float = 0.7,
    classes: list[int] | None = None,
    max_det: int = 300,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Выход модели -> (xyxy в пикселях исходного кадра, оценки, классы). shape = (h, w)."""
    pred = np.asarray(output)
    if pred.ndim == 3:
        pred = pred[0]
    pred = pred.T  # (N, 4+nc)
    scores_all = pred[:, 4:]
    cls = scores_all.argmax(axis=1)
    score = scores_all.max(axis=1)
    mask = score > conf
    if classes is not None:
        mask &= np.isin(cls, classes)
    pred, cls, score = pred[mask], cls[mask], score[mask]
    cx, cy, w, h = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]
    boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
    keep = nms(boxes, score, cls, iou)[:max_det]
    boxes, cls, score = boxes[keep], cls[keep], score[keep]
    boxes[:, [0, 2]] = (boxes[:, [0, 2]] - pad[0]) / scale
    boxes[:, [1, 3]] = (boxes[:, [1, 3]] - pad[1]) / scale
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, shape[1])
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, shape[0])
    return boxes, score, cls


def model_names(session) -> dict[int, str]:
    """Имена классов из метаданных ONNX, записанных ultralytics; пусто, если их нет."""
    raw = session.get_modelmeta().custom_metadata_map.get("names")
    return {int(k): v for k, v in ast.literal_eval(raw).items()} if raw else {}


class OnnxDetector:
    """Кадр BGR -> детекции через сессию onnxruntime (см. infer.make_session)."""

    def __init__(self, session, imgsz: int = 640):
        self.session = session
        self.imgsz = imgsz
        self.input_name = session.get_inputs()[0].name
        self.names = model_names(session)

    def __call__(self, frame, conf: float, classes: list[int] | None = None, iou: float = 0.7):
        tensor, scale, pad = letterbox(frame, self.imgsz)
        output = self.session.run(None, {self.input_name: tensor})[0]
        return decode(output, scale, pad, frame.shape[:2], conf, iou, classes)


def decode_pose(
    output: np.ndarray,
    scale: float,
    pad: tuple[float, float],
    shape: tuple[int, int],
    conf: float,
    iou: float = 0.7,
    max_det: int = 100,
) -> list[tuple]:
    """Выход yolo11n-pose (1, 5+17*3, N) -> [(xyxy в пикселях кадра, [(x, y, conf)] * 17)]."""
    pred = np.asarray(output)
    if pred.ndim == 3:
        pred = pred[0]
    pred = pred.T  # (N, 56): cx, cy, w, h, score, 17 x (x, y, conf)
    pred = pred[pred[:, 4] > conf]
    if not len(pred):
        return []
    cx, cy, w, h = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]
    boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
    keep = nms(boxes, pred[:, 4], np.zeros(len(pred), dtype=int), iou)[:max_det]
    poses = []
    for i in keep:
        x1, x2 = ((boxes[i, [0, 2]] - pad[0]) / scale).clip(0, shape[1])
        y1, y2 = ((boxes[i, [1, 3]] - pad[1]) / scale).clip(0, shape[0])
        kp = pred[i, 5:].reshape(-1, 3)
        points = [((px - pad[0]) / scale, (py - pad[1]) / scale, float(pc)) for px, py, pc in kp]
        poses.append(((float(x1), float(y1), float(x2), float(y2)), points))
    return poses


class OnnxPoseDetector:
    """Позы через сессию onnxruntime / LinQ (отдельная программа pose.onnx)."""

    def __init__(self, session, imgsz: int = 640):
        self.session, self.imgsz = session, imgsz
        self.input_name = session.get_inputs()[0].name

    def __call__(self, frame, conf: float, iou: float = 0.7) -> list[tuple]:
        tensor, scale, pad = letterbox(frame, self.imgsz)
        output = self.session.run(None, {self.input_name: tensor})[0]
        return decode_pose(output, scale, pad, frame.shape[:2], conf, iou)


def decode_obb(
    output: np.ndarray,
    scale: float,
    pad: tuple[float, float],
    shape: tuple[int, int],
    conf: float,
    iou: float = 0.7,
    max_det: int = 300,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Сырой выход yolo11n_obb (1, 4+nc+1, N): cx, cy, w, h, оценки классов, угол (рад).

    Возвращает (xyxy осевая оболочка повёрнутой рамки в пикселях кадра, оценки, классы,
    повёрнутые рамки (cx, cy, w, h, угол) в пикселях кадра). NMS — по осевым оболочкам
    (упрощение: для повёрнутых рамок IoU считается грубее, чем в ultralytics).
    """
    pred = np.asarray(output)
    if pred.ndim == 3:
        pred = pred[0]
    pred = pred.T  # (N, 4+nc+1)
    scores_all, angle = pred[:, 4:-1], pred[:, -1]
    cls = scores_all.argmax(axis=1)
    score = scores_all.max(axis=1)
    mask = score > conf
    pred, cls, score, angle = pred[mask], cls[mask], score[mask], angle[mask]
    cx, cy, w, h = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]
    cos, sin = np.abs(np.cos(angle)), np.abs(np.sin(angle))
    half_w = (w * cos + h * sin) / 2
    half_h = (w * sin + h * cos) / 2
    hull = np.stack([cx - half_w, cy - half_h, cx + half_w, cy + half_h], axis=1)
    keep = nms(hull, score, cls, iou)[:max_det]
    hull, cls, score = hull[keep], cls[keep], score[keep]
    rotated = np.stack([cx[keep], cy[keep], w[keep], h[keep], angle[keep]], axis=1)
    rotated[:, 0] = (rotated[:, 0] - pad[0]) / scale
    rotated[:, 1] = (rotated[:, 1] - pad[1]) / scale
    rotated[:, 2:4] /= scale
    hull[:, [0, 2]] = ((hull[:, [0, 2]] - pad[0]) / scale).clip(0, shape[1])
    hull[:, [1, 3]] = ((hull[:, [1, 3]] - pad[1]) / scale).clip(0, shape[0])
    return hull, score, cls, rotated
