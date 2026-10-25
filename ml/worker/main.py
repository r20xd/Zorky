"""Инференс-воркер: видео -> детекции -> правила -> события в бэкенд.

Из папки ml/ при запущенном бэкенде:
    py -m worker.main --fake --camera cam-1                                   # без модели и видео
    py -m worker.main --video ../demo/videos/cam1.mp4 --camera cam-1          # YOLO на CPU

Реальный режим: CPU или GPU, на LinQ — через --device linq (нужен .onnx, см. README.md).
"""

from __future__ import annotations

import argparse
import contextlib
import math
import os
import sys
import time
import urllib.error
from datetime import datetime
from pathlib import Path

from .client import BackendClient
from .config import PERSON_CONF, PPE_CONF, REPEAT_S, TRACKER_DEFAULTS
from .dedup import OverlapDeduper
from .events import make_event
from .images import image_paths, read_images
from .infer import DeviceUnavailable, check_device, load_model
from .metrics import MetricsReporter, pose_clock
from .overlay import draw_detections
from .pipeline import COCO_TRACKED, Detection, find_violations, parse_ppe, parse_tracks
from .realtime import LatestFrames
from .rules import Debouncer, build_rules
from .rules.base import needs_pose
from .schedule import active_zones

ZONES_REFRESH_S = 5.0
PREVIEW_FPS = 5.0
PREVIEW_WIDTH = 640
FLAG_S = 6.0  # сколько секунд после события нарушитель красный на превью
SNAPSHOT_WIDTH = 960


class PreviewThrottle:
    """Пропускает не чаще fps раз в секунду (время передаётся снаружи)."""

    def __init__(self, fps: float = PREVIEW_FPS) -> None:
        self.interval = 1 / fps
        self.last = float("-inf")

    def ready(self, now: float) -> bool:
        if now - self.last >= self.interval:
            self.last = now
            return True
        return False


def encode_jpeg(
    frame,
    width: int = PREVIEW_WIDTH,
    quality: int = 70,
    box: tuple | None = None,
    detections=None,
    violators: set[int] | None = None,
) -> bytes:
    """Кадр (numpy BGR) -> готовый JPEG для бэкенда, уменьшенный до width.

    box — рамка нарушения [x1, y1, x2, y2] в 0..1, рисуется на копии кадра.
    detections — найденные люди и СИЗ: на превью рисуются рамки с подписью (worker.overlay);
    violators — track_id нарушителей, их рамки красные.
    """
    import cv2

    if detections:
        frame = draw_detections(frame, detections, violators)
    height, full = frame.shape[:2]
    if box is not None:
        frame = frame.copy()
        p1 = (round(box[0] * full), round(box[1] * height))
        p2 = (round(box[2] * full), round(box[3] * height))
        cv2.rectangle(frame, p1, p2, (0, 0, 255), max(2, full // 300))
    if full > width:
        frame = cv2.resize(frame, (width, round(height * width / full)))
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("не удалось закодировать кадр в JPEG")
    return buf.tobytes()


def jpeg_maker(frame, detections):
    """Колбэк кадра: снимок с рамкой нарушения (box) или превью с разметкой (overlay)."""

    def make_jpeg(box=None, overlay=False, violators=None) -> bytes:
        return encode_jpeg(
            frame,
            width=SNAPSHOT_WIDTH if box else PREVIEW_WIDTH,
            box=box,
            detections=detections if overlay else None,
            violators=violators,
        )

    return make_jpeg


def send_preview(
    client,
    camera_id: str,
    make_jpeg,
    throttle: PreviewThrottle,
    now: float,
    violators: set[int] | None = None,
) -> bool:
    """Шлёт превью, если пора; сбой превью не должен ронять воркер."""
    if make_jpeg is None or not throttle.ready(now):
        return False
    try:
        client.post_frame(camera_id, make_jpeg(overlay=True, violators=violators))
    except (urllib.error.URLError, OSError):
        return False
    return True


def send_snapshot(client, make_jpeg, bbox) -> str | None:
    """Грузит JPEG кадра с рамкой, возвращает url; сбой снимка не должен терять событие."""
    if make_jpeg is None:
        return None
    try:
        return client.post_snapshot(make_jpeg(box=tuple(bbox)))
    except (urllib.error.URLError, OSError, KeyError, ValueError):
        return None


def fetch_enabled_rules(client, camera_id: str) -> tuple[bool, list[str] | None]:
    """Выбор специалиста по охране труда: (получили ли ответ, коды правил или None — по умолчанию).

    Бэкенд недоступен или камеры в нём нет — ответа нет, воркер остаётся на прежних правилах."""
    try:
        answer = client.get_rules(camera_id)
    except (urllib.error.URLError, OSError, ValueError):
        return False, None
    if not isinstance(answer, dict) or "enabled" not in answer:
        return False, None
    return True, answer["enabled"]


def send_metrics(client, reporter: MetricsReporter, now: float, skipped: int = 0) -> bool:
    """Шлёт метрики, если пора; сбой метрик не должен ронять воркер.

    skipped — сколько кадров пропущено за окно (только в лог, не в метрики).
    """
    body = reporter.report(now)
    if body is None:
        return False
    pose_part = "" if reporter.last_pose_ms is None else f" (поза {reporter.last_pose_ms} мс)"
    print(
        f"FPS {body['fps']}, инференс {body['inference_ms']} мс"
        f"{pose_part}, пропущено кадров {skipped}",
        flush=True,
    )
    try:
        client.post_metrics(body)
    except (urllib.error.URLError, OSError):
        return False
    return True


RETRY_ATTEMPTS = 3
RETRY_DELAY_S = 1.0
RECONNECT_DELAY_S = 2.0


def with_retries(call, what: str, attempts: int = RETRY_ATTEMPTS, sleep=time.sleep):
    """Вызывает call(); при сетевом сбое повторяет, потом логирует и возвращает None."""
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except (urllib.error.URLError, OSError) as err:
            print(f"{what}: сбой {attempt}/{attempts}: {err}", file=sys.stderr, flush=True)
            if attempt < attempts:
                sleep(RETRY_DELAY_S * attempt)
    return None


def is_webcam(source: str) -> bool:
    return str(source).strip().isdigit()


def is_stream(source: str) -> bool:
    return "://" in str(source) or is_webcam(source)


def open_source(cv2, source: str):
    """Строка из одних цифр - номер веб-камеры (int), остальное - файл или адрес как есть."""
    text = str(source).strip()
    return cv2.VideoCapture(int(text) if text.isdigit() else source)


def read_frames(open_capture, source: str, loop: bool = False, sleep=time.sleep):
    """Кадры из источника; обрыв потока — переподключение, конец файла с loop — заново.

    open_capture() -> объект с read() и release(). Без loop файл заканчивается один раз.
    """
    stream = is_stream(source)
    while True:
        capture = open_capture()
        got = 0
        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                got += 1
                yield frame
        finally:
            capture.release()
        if not stream and not loop:
            return
        if stream or got == 0:
            print(f"источник {source} оборвался, переподключение", file=sys.stderr, flush=True)
            sleep(RECONNECT_DELAY_S)


def fake_detections(frame_id: int) -> list[Detection]:
    """Синтетический человек идёт слева направо — проверка цепочки без модели и видео."""
    x = (frame_id % 200) / 200 * 0.9
    return [
        Detection("person", (x, 0.4, x + 0.1, 0.9), 0.9, track_id=1),
        Detection("head", (x + 0.03, 0.4, x + 0.07, 0.5), 0.8, track_id=-1),
        Detection("cigarette", (x + 0.05, 0.47, x + 0.08, 0.5), 0.7, track_id=-1),
    ]


def write_tracker_yaml(path: str, **overrides) -> str:
    """Конфиг трекера ultralytics (ByteTrack) с нашими значениями; возвращает path."""
    params = {**TRACKER_DEFAULTS, **overrides}
    lines = [
        f"{key}: {str(value).lower() if isinstance(value, bool) else value}"
        for key, value in params.items()
    ]
    with open(path, "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")
    return path


def yolo_detections(
    model, frame, tracker: str = "bytetrack.yaml", conf: float = PERSON_CONF, imgsz: int = 640
) -> list[Detection]:
    """Люди с трекингом из ultralytics; координаты нормируются к 0..1."""
    height, width = frame.shape[:2]
    result = model.track(
        frame,
        persist=True,
        classes=list(COCO_TRACKED),
        tracker=tracker,
        conf=conf,
        imgsz=imgsz,
        verbose=False,
    )[0]
    boxes = result.boxes
    ids = None if boxes.id is None else boxes.id.int().tolist()
    return parse_tracks(
        boxes.xyxy.tolist(), boxes.conf.tolist(), ids, width, height, boxes.cls.int().tolist()
    )


def ppe_detections(model, frame, conf: float = PPE_CONF, imgsz: int = 640) -> list[Detection]:
    """Каска / голова и др. СИЗ второй моделью (без трекинга)."""
    height, width = frame.shape[:2]
    result = model.predict(frame, conf=conf, imgsz=imgsz, verbose=False)[0]
    boxes = result.boxes
    return parse_ppe(
        boxes.xyxy.tolist(),
        boxes.conf.tolist(),
        boxes.cls.int().tolist(),
        result.names,
        width,
        height,
    )


def pose_detections(model, frame, conf: float = PERSON_CONF, imgsz: int = 640) -> list[tuple]:
    """Позы yolo11n-pose: [(bbox 0..1, ключевые точки COCO-17 в пикселях)] без трекинга."""
    result = model.predict(frame, conf=conf, imgsz=imgsz, verbose=False)[0]
    if result.keypoints is None or result.boxes is None:
        return []
    height, width = frame.shape[:2]
    confs = result.keypoints.conf
    poses_xy = result.keypoints.xy.tolist()
    poses = []
    for box, xy, kc in zip(
        result.boxes.xyxy.tolist(),
        poses_xy,
        confs.tolist() if confs is not None else [[1.0] * 17] * len(poses_xy),
        strict=True,
    ):
        norm = (box[0] / width, box[1] / height, box[2] / width, box[3] / height)
        poses.append((norm, [(x, y, c) for (x, y), c in zip(xy, kc, strict=True)]))
    return poses


def attach_poses(people: list[Detection], poses: list[tuple], width: int, height: int, min_iou=0.4):
    """Кладёт позу в трек-человека с наибольшим пересечением рамок (IoU >= min_iou)."""
    from dataclasses import replace

    def iou(a, b):
        w = min(a[2], b[2]) - max(a[0], b[0])
        h = min(a[3], b[3]) - max(a[1], b[1])
        if w <= 0 or h <= 0:
            return 0.0
        inter = w * h
        area = lambda r: (r[2] - r[0]) * (r[3] - r[1])  # noqa: E731
        return inter / (area(a) + area(b) - inter)

    result, used = [], set()
    for person in people:
        best, best_iou = None, min_iou
        if person.cls == "person":
            for i, (box, _) in enumerate(poses):
                value = iou(person.bbox, box)
                if i not in used and value >= best_iou:
                    best, best_iou = i, value
        if best is None:
            result.append(person)
            continue
        used.add(best)
        kp = tuple((x / width, y / height, c) for x, y, c in poses[best][1])
        result.append(replace(person, keypoints=kp))
    return result


class OnnxPose:
    """Поза на ONNX (onnxruntime или LinQ): кадр -> [(bbox 0..1, точки в пикселях)]."""

    def __init__(self, session, conf: float = PERSON_CONF, imgsz: int = 640):
        from .detect import OnnxPoseDetector

        self.detector, self.conf = OnnxPoseDetector(session, imgsz), conf

    def __call__(self, frame) -> list[tuple]:
        height, width = frame.shape[:2]
        return [
            ((b[0] / width, b[1] / height, b[2] / width, b[3] / height), kp)
            for b, kp in self.detector(frame, self.conf)
        ]


class PosePeople:
    """Люди с трекингом + позы второй моделью (yolo11n-pose); нужна только для distress_signal.

    pose_model — вызываемое «кадр -> позы» (OnnxPose) или модель ultralytics (.pt).
    Время позы копится отдельно (metrics.pose_clock) и попадает в лог метрик.
    """

    def __init__(self, people, pose_model):
        self.people, self.pose_model = people, pose_model

    def __call__(self, frame) -> list[Detection]:
        found = self.people(frame)
        height, width = frame.shape[:2]
        start = time.perf_counter()
        if isinstance(self.pose_model, OnnxPose) or not hasattr(self.pose_model, "predict"):
            poses = self.pose_model(frame)
        else:  # модель ultralytics тоже вызываемая, но её __call__ отдаёт сырые Results
            poses = pose_detections(self.pose_model, frame)
        pose_clock.add(time.perf_counter() - start)
        return attach_poses(found, poses, width, height)


def args_for_camera(args, camera_id: str):
    """args камеры: модель позы остаётся, только если для камеры включено правило по позе."""
    if not getattr(args, "pose_model", None) or needs_pose(camera_id):
        return args
    return argparse.Namespace(**{**vars(args), "pose_model": None})


class OnnxPeople:
    """Люди: свой ONNX-детектор + свой ByteTrack, без ultralytics."""

    def __init__(self, session, conf: float = PERSON_CONF, imgsz: int = 640):
        from .bytetrack import ByteTracker
        from .detect import OnnxDetector

        self.detector = OnnxDetector(session, imgsz)
        params = {k: v for k, v in TRACKER_DEFAULTS.items() if k != "tracker_type"}
        self._new_tracker = lambda: ByteTracker(**params)
        self.trackers = {0: self._new_tracker()}
        self.tracker = self.trackers[0]
        self.conf = conf

    def __call__(self, frame) -> list[Detection]:
        height, width = frame.shape[:2]
        boxes, scores, classes = self.detector(
            frame, min(self.conf, self.tracker.low + 1e-6), list(COCO_TRACKED)
        )
        found = []
        for class_id in sorted(set(classes.tolist())):
            # Люди и каждый класс техники — свой трекер: id не смешиваются между классами.
            tracker = self.trackers.get(class_id)
            if tracker is None:
                tracker = self.trackers[class_id] = self._new_tracker()
            mask = classes == class_id
            tracks = tracker.update(boxes[mask], scores[mask])
            found += parse_tracks(
                [t[0].tolist() for t in tracks],
                [float(t[1]) for t in tracks],
                [t[2] + class_id * 100000 for t in tracks],
                width,
                height,
                [class_id] * len(tracks),
            )
        return found


class OnnxPpe:
    """Модель СИЗ на своём ONNX-детекторе, без трекинга."""

    def __init__(self, session, conf: float = PPE_CONF, imgsz: int = 640):
        from .detect import OnnxDetector

        self.detector = OnnxDetector(session, imgsz)
        self.conf = conf

    def __call__(self, frame) -> list[Detection]:
        height, width = frame.shape[:2]
        boxes, scores, classes = self.detector(frame, self.conf)
        return parse_ppe(
            boxes.tolist(), scores.tolist(), classes.tolist(), self.detector.names, width, height
        )


def default_ppe_weights() -> str | None:
    """Веса СИЗ без --ppe-weights: MOSPROM_PPE_WEIGHTS, models/best.onnx репозитория,
    ~/.mosprom/models/best.onnx, ml/weights.

    Без весов СИЗ правила no_helmet, no_respirator и smoking молчат, поэтому демо ищет их само.
    """
    env = os.environ.get("MOSPROM_PPE_WEIGHTS")
    candidates = [Path(env)] if env else []
    candidates += [
        Path(__file__).resolve().parents[2] / "models" / "best.onnx",
        Path.home() / ".mosprom" / "models" / "best.onnx",
        Path(__file__).resolve().parents[1] / "weights" / "best.pt",
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def make_detectors(args, cache: dict | None = None):
    """(люди, СИЗ или None): .onnx — свой путь на onnxruntime, иначе ultralytics (.pt).

    cache — общий между камерами: сессии onnxruntime и модель СИЗ грузятся один раз.
    Состояние трекера у каждой камеры своё: OnnxPeople создаётся заново, .pt-модель людей
    грузится на камеру (трекер ultralytics живёт внутри модели).
    """
    from .infer import make_session

    cache = {} if cache is None else cache

    def is_onnx(path):
        """Путь, который читает свой ONNX-слой: .onnx, а на LinQ ещё и готовая программа .tpu."""
        suffixes = (".onnx", ".tpu") if args.device == "linq" else (".onnx",)
        return bool(path) and str(path).lower().endswith(suffixes)

    def session(path):
        if path not in cache:
            device = args.device
            if device == "linq" and not str(path).lower().endswith(".tpu"):
                device = "cpu"  # на LinQ идёт скомпилированная программа .tpu, обычный .onnx - CPU
            cache[path] = make_session(path, device)
        return cache[path]

    if is_onnx(args.model):
        people = OnnxPeople(session(args.model))
    else:
        model = load_model(args.model, args.device)
        people = lambda frame: yolo_detections(model, frame)  # noqa: E731
    pose_model = getattr(args, "pose_model", None)
    if pose_model:
        if pose_model not in cache:
            if is_onnx(pose_model):
                cache[pose_model] = OnnxPose(session(pose_model))
            else:
                cache[pose_model] = load_model(pose_model, args.device)
        people = PosePeople(people, cache[pose_model])
    ppe = None
    ppe_weights = args.ppe_weights or default_ppe_weights()
    if is_onnx(ppe_weights):
        ppe = OnnxPpe(session(ppe_weights))
    elif ppe_weights:
        if ppe_weights not in cache:
            cache[ppe_weights] = load_model(ppe_weights, args.device)
        ppe_model = cache[ppe_weights]
        ppe = lambda frame: ppe_detections(ppe_model, frame)  # noqa: E731
    return people, ppe


def fake_frames(args: argparse.Namespace, offset: int = 0):
    """Тройки (номер кадра, детекции, None) синтетики; offset сдвигает «человека» между камерами."""
    frame_id = 0
    while args.max_frames is None or frame_id < args.max_frames:
        yield frame_id, fake_detections(frame_id + offset), None
        frame_id += 1
        time.sleep(1 / args.fps)


def video_frames(args: argparse.Namespace, video: str, people, ppe, infer_lock, dropped: list):
    """Тройки (номер кадра, детекции, функция -> JPEG) из видео одной камеры.

    Инференс идёт под infer_lock: камеры делят модель и ходят в неё по очереди.
    dropped — список, в [0] кладётся функция «сколько кадров пропущено».
    """
    import cv2

    interval = 0.0
    if not is_stream(video):
        probe = open_source(cv2, video)
        fps = probe.get(cv2.CAP_PROP_FPS)
        probe.release()
        interval = 1 / fps if fps and fps > 0 else 1 / 25
    source = LatestFrames(read_frames(lambda: open_source(cv2, video), video, args.loop), interval)
    dropped[0] = lambda: source.dropped
    frame_id = 0
    for frame in source:
        if args.max_frames is not None and frame_id >= args.max_frames:
            break
        with infer_lock:
            detections = people(frame)
            if ppe is not None:
                detections += ppe(frame)
        yield (
            frame_id,
            detections,
            jpeg_maker(frame, detections),
        )
        frame_id += 1
    source.close()


def image_frames(args: argparse.Namespace, folder: str, people, ppe, infer_lock, dropped: list):
    """Картинки проходят тот же детектор и цикл событий, что видеокадры."""
    dropped[0] = lambda: 0
    source = read_images(folder, args.fps, args.loop)
    try:
        frame_id = 0
        while args.max_frames is None or frame_id < args.max_frames:
            try:
                frame = next(source)
            except StopIteration:
                return
            with infer_lock:
                detections = people(frame)
                if ppe is not None:
                    detections += ppe(frame)
            yield (
                frame_id,
                detections,
                jpeg_maker(frame, detections),
            )
            frame_id += 1
    finally:
        source.close()


def run_camera(client, camera_id: str, device: str, items, dropped=lambda: 0) -> None:
    """Цикл одной камеры: метрики, превью, зоны, события. Состояние (трекер, Debouncer,
    метрики, зоны) своё у каждой камеры."""
    debouncer = Debouncer(repeat_s=REPEAT_S)
    deduper = OverlapDeduper()
    rules = build_rules(camera_id)
    zones: list[dict] = []
    zones_at = float("-inf")
    enabled: list[str] | None = None  # выбор специалиста; None — набор из rules.yaml
    throttle = PreviewThrottle()
    flagged: dict[int, float] = {}  # track_id нарушителя -> до какого момента красный на превью
    reporter = MetricsReporter(camera_id, device)
    pose_clock.take()  # сбросить накопленное другой камерой этого потока
    frame_start = time.perf_counter()
    dropped_at_report = 0
    for frame_id, detections, make_jpeg in items:
        now = time.monotonic()
        reporter.frame(time.perf_counter() - frame_start, pose_clock.take())  # кадр + инференс
        window = reporter.started
        send_metrics(client, reporter, now, dropped() - dropped_at_report)
        if reporter.started != window:  # окно отчёта закрыто
            dropped_at_report = dropped()
        flagged = {track: until for track, until in flagged.items() if until > now}
        send_preview(client, camera_id, make_jpeg, throttle, now, set(flagged))
        if now - zones_at > ZONES_REFRESH_S:
            got = with_retries(lambda: client.get_zones(camera_id), "зоны")
            zones = zones if got is None else got
            zones_at = now
            known, chosen = fetch_enabled_rules(client, camera_id)
            if known and chosen != enabled:
                enabled = chosen
                rules = (
                    build_rules(camera_id, enabled=enabled)
                    if enabled is not None
                    else build_rules(camera_id)
                )
                names = "по умолчанию" if enabled is None else ", ".join(enabled) or "ничего"
                print(f"[{camera_id}] правила: {names}", flush=True)
        working = active_zones(zones, datetime.now().time())  # зоны по расписанию
        for violation in find_violations(detections, working, debouncer, now, rules):
            detection = violation.detection
            if not deduper.allow(violation.type, detection.bbox, detection.track_id, now):
                continue
            event = make_event(
                camera_id=camera_id,
                frame_id=frame_id,
                violation=violation.type,
                bbox=detection.bbox,
                confidence=detection.confidence,
                track_id=detection.track_id if detection.track_id >= 0 else None,
                zone_id=violation.zone_id,
                severity=violation.severity,
                snapshot_url=send_snapshot(client, make_jpeg, detection.bbox),
            )
            with_retries(lambda event=event: client.post_event(event), "событие")
            reporter.event(time.perf_counter() - frame_start)
            if detection.track_id >= 0:
                flagged[detection.track_id] = now + FLAG_S
            print(f"[{camera_id}] -> {violation.type} track={detection.track_id}", flush=True)
        frame_start = time.perf_counter()


def run_cameras(client, device: str, jobs: list[tuple[str, object, object]]) -> None:
    """Камеры параллельно, по потоку на камеру; jobs — (camera_id, items, dropped).

    Одна камера — в текущем потоке. Ждёт все потоки; Ctrl+C прерывает ожидание.
    """
    import threading

    if len(jobs) == 1:
        camera_id, items, dropped = jobs[0]
        run_camera(client, camera_id, device, items, dropped)
        return
    errors = []
    stop = threading.Event()

    def active_items(items):
        for item in items:
            if stop.is_set():
                return
            yield item

    def camera_job(camera_id, items, dropped):
        try:
            run_camera(client, camera_id, device, active_items(items), dropped)
        except Exception as error:
            errors.append(error)
            stop.set()
        finally:
            close = getattr(items, "close", None)
            if close is not None:
                close()

    threads = [
        threading.Thread(target=camera_job, args=(camera_id, items, dropped), daemon=True)
        for camera_id, items, dropped in jobs
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        while thread.is_alive():
            thread.join(0.5)
    if errors:
        raise errors[0]


def run_from_backend(args, parser) -> int:
    """Режим --cameras-from-backend: камеры живут и меняются вместе со списком бэкенда."""
    import threading

    from .supervisor import CameraSupervisor, mask_source, stoppable

    try:
        check_device(args.device)
    except DeviceUnavailable as error:
        parser.error(str(error))
    client = BackendClient(args.backend)
    cache: dict = {}
    # LinQ: у каждой камеры своя сессия (соединение с брокером не делится между потоками) и нет
    # общего замка - запросы разных камер идут на ускоритель одновременно
    linq = args.device == "linq"
    infer_lock = contextlib.nullcontext() if linq else threading.Lock()

    def start(camera_id: str, source: str, stop: threading.Event):
        people, ppe = make_detectors(args_for_camera(args, camera_id), {} if linq else cache)
        dropped = [lambda: 0]
        items = stoppable(video_frames(args, source, people, ppe, infer_lock, dropped), stop)

        def target():
            try:
                run_camera(client, camera_id, args.device, items, lambda: dropped[0]())
            except Exception as err:  # noqa: BLE001
                print(
                    f"[{camera_id}] поток упал: {mask_source(err)}",
                    file=sys.stderr,
                    flush=True,
                )

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        return thread

    supervisor = CameraSupervisor(client, start)
    try:
        supervisor.run()
    except KeyboardInterrupt:
        pass
    finally:
        supervisor.stop_all()
    return 0


def main_offline(args, parser) -> int:
    """--offline: каждый файл целиком, события в JSONL; ни бэкенда, ни часов компьютера."""
    from datetime import datetime, timezone
    from pathlib import Path

    from .offline import OFFLINE_START, run_offline

    if not args.video:
        parser.error("--offline: нужен --video")
    start = OFFLINE_START
    if args.start:
        start = datetime.fromisoformat(args.start.replace("Z", "+00:00"))
        start = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    out = Path(args.out)
    cache: dict = {}
    for camera_id, video in zip(args.camera, args.video, strict=True):
        people, ppe = make_detectors(args_for_camera(args, camera_id), cache)
        n = run_offline(video, camera_id, people, ppe, out, start)
        print(f"[{camera_id}] {video}: {n} событий -> {out}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Инференс-воркер mosprom")
    parser.add_argument(
        "--camera",
        action="append",
        help="id камеры, например cam-1; для нескольких видео повторяется в том же порядке",
    )
    parser.add_argument("--backend", default=os.environ.get("BACKEND_URL", "http://localhost:8080"))
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--video", action="append", help="путь к видео или RTSP-адрес; повторяется по камере"
    )
    source.add_argument("--images", action="append", help="папка JPG/PNG; повторяется по камере")
    source.add_argument("--fake", action="store_true", help="синтетические детекции")
    source.add_argument(
        "--cameras-from-backend",
        action="store_true",
        help="камеры и их source из GET /api/cameras (WORKER_TOKEN), список обновляется раз в 30 с",
    )
    parser.add_argument(
        "--model", default="yolo11n.pt", help="веса YOLO: .pt (скачаются сами) или .onnx"
    )
    parser.add_argument(
        "--pose-model",
        "--pose-weights",
        dest="pose_model",
        help="yolo11n-pose (.pt или .onnx) для правил по позе (distress_signal); "
        "грузится, только если правило включено в rules.yaml для камеры",
    )
    parser.add_argument(
        "--ppe-weights",
        help="веса модели СИЗ (.pt или .onnx); без флага - MOSPROM_PPE_WEIGHTS, "
        "~/.mosprom/models/best.onnx или ml/weights/best.pt",
    )
    parser.add_argument("--device", choices=["linq", "cpu", "gpu"], default="cpu")
    parser.add_argument(
        "--fps", type=float, default=10.0, help="темп изображений и синтетики, кадров/с"
    )
    parser.add_argument(
        "--loop", action="store_true", help="крутить видеофайл или папку изображений по кругу"
    )
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="файл целиком, каждый кадр, события в JSONL (--out), бэкенд не нужен; "
        "время события = --start + кадр / FPS, зоны — eval/zones/<файл>.json",
    )
    parser.add_argument("--out", default="events.jsonl", help="--offline: журнал событий (JSONL)")
    parser.add_argument(
        "--start", help="--offline: ISO-время начала ролика (по умолчанию 2000-01-01T00:00:00Z)"
    )
    args = parser.parse_args(argv)

    if args.cameras_from_backend:
        if args.camera:
            parser.error("--cameras-from-backend не сочетается с --camera")
        return run_from_backend(args, parser)
    if not args.camera:
        parser.error("нужен --camera")
    if len(set(args.camera)) != len(args.camera):
        parser.error("--camera: id камер должны быть разными")
    if args.video and len(args.video) != len(args.camera):
        parser.error("--video и --camera должны повторяться одинаковое число раз")
    if not math.isfinite(args.fps) or args.fps <= 0:
        parser.error("--fps должен быть конечным положительным числом")
    if args.images:
        if len(args.images) != len(args.camera):
            parser.error("--images и --camera должны повторяться одинаковое число раз")
        try:
            for folder in args.images:
                image_paths(folder)
        except ValueError as error:
            parser.error(str(error))
    if not args.fake:
        try:
            check_device(args.device)
        except DeviceUnavailable as error:
            parser.error(str(error))
    if args.offline:
        return main_offline(args, parser)
    client = BackendClient(args.backend)
    jobs = []
    if args.fake:
        for index, camera_id in enumerate(args.camera):
            jobs.append((camera_id, fake_frames(args, offset=index * 40), lambda: 0))
    else:
        import threading

        cache: dict = {}
        infer_lock = threading.Lock()
        sources = args.images if args.images else args.video
        frame_source = image_frames if args.images else video_frames
        for camera_id, source_path in zip(args.camera, sources, strict=True):
            people, ppe = make_detectors(args_for_camera(args, camera_id), cache)
            dropped = [lambda: 0]
            items = frame_source(args, source_path, people, ppe, infer_lock, dropped)
            jobs.append((camera_id, items, lambda dropped=dropped: dropped[0]()))
    try:
        run_cameras(client, args.device, jobs)
    except KeyboardInterrupt:
        pass
    except ValueError as error:
        print(str(error), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
