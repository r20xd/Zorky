"""Демо с живым видео: ролики по кругу вместо камер, нарушения приходят по одному и редко.

Мок ML-воркера для показа: кадры уходят в /api/cameras/<id>/frame (превью в вебе),
события — в /api/events с кадром-снимком и рамкой человека. Сначала нарушений нет,
первое приходит через --first секунд, дальше раз в --gap-min..--gap-max секунд.
Нарушения двух видов: просто событие поверх спокойного ролика или ролик, где человек
нарушает на глазах (падение) — камера переключается на него, событие уходит в момент
нарушения. Рамки людей посчитаны заранее (demo/video_boxes.json), модель не нужна.
Нужен opencv (ml/.venv):
    py demo/replay_video.py --cam cam-1=demo/videos/cam1.mp4 --cam cam-2=demo/videos/cam2.mp4
Ролики с нарушениями (demo/videos/viol1..3.mp4) подхватываются сами, если лежат рядом.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).parents[1] / "ml"))
from worker.overlay import draw_detections  # noqa: E402
from worker.rules.base import Detection  # noqa: E402

BOXES_FILE = Path(__file__).with_name("video_boxes.json")
REGISTRY_FILE = Path(__file__).parents[1] / "contracts" / "violation_types.json"
CENTER_BOX = [0.35, 0.1, 0.65, 0.95]
REPEAT_PAUSE_S = 9.0
FLAG_S = 6.0  # сколько секунд после события человек подсвечен красным на превью

# Ролики с нарушением: имя -> события (секунда ролика, тип)
CLIPS = {
    "viol1": [(4.0, "fall")],
    "viol2": [(4.0, "fall")],
    "viol3": [(8.5, "fall"), (14.0, "immobile")],
}

# (камера, что, сколько раз повторить с паузой) — по кругу. Одно и то же не повторяем:
# длящееся нарушение — одно сообщение, повтор в окне склеивается в «×N» (критерий кейса).
# «clip:<имя>» — камера показывает ролик с нарушением, события уходят из него.
SCENARIO = [
    ("cam-2", "no_helmet", 1),
    ("cam-1", "clip:viol1", 0),
    ("cam-1", "no_respirator", 0),
    ("cam-2", "clip:viol2", 0),
    ("cam-1", "danger_zone", 2),
    ("cam-2", "smoking", 0),
    ("cam-1", "clip:viol3", 0),
]


def load_severity() -> dict[str, str]:
    """Критичность типов из реестра контракта: код -> low|medium|high|critical."""
    try:
        types = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))["types"]
    except (OSError, ValueError, KeyError):
        return {}
    return {t["code"]: t["severity"] for t in types}


SEVERITY = load_severity()
_track_ids = itertools.count(1)
_frame_ids = itertools.count(100, 25)


@dataclass
class Clip:
    name: str
    path: Path
    events: list[tuple[float, str, str]]
    boxes: list[list[float]]


@dataclass
class CamState:
    camera_id: str
    path: Path
    boxes: list[list[float]] = field(default_factory=list)
    frame: object = None
    pos_s: float = 0.0
    active_boxes: list[list[float]] = field(default_factory=list)
    clip_request: Clip | None = None
    flagged_until: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def tracked_box(self) -> list[float] | None:
        """Рамка человека в текущий момент ролика (ближайший шаг таймлайна) или None."""
        with self.lock:
            pos, boxes = self.pos_s, self.active_boxes
        if not boxes:
            return None
        near = min(boxes, key=lambda b: abs(b[0] - pos))
        return near[1:5] if abs(near[0] - pos) <= 1.0 else None

    def person_box(self) -> list[float]:
        return self.tracked_box() or CENTER_BOX

    def flag(self) -> None:
        with self.lock:
            self.flagged_until = time.monotonic() + FLAG_S

    def preview(self, frame):
        """Кадр для превью: рамка человека, красная у нарушителя (как разметка воркера)."""
        box = self.tracked_box()
        if box is None:
            return frame
        with self.lock:
            flagged = time.monotonic() < self.flagged_until
        person = Detection(cls="person", bbox=tuple(box), confidence=0.9, track_id=1)
        return draw_detections(frame, [person], violators={1} if flagged else None)


def request(backend: str, path: str, data: bytes, content_type: str, timeout: float = 5.0) -> bytes:
    headers = {"Content-Type": content_type}
    token = os.environ.get("WORKER_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{backend.rstrip('/')}{path}", data=data, headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def jpeg(frame, quality: int = 72) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""


def stream(cam: CamState, backend: str, send_fps: float, width: int, stop: threading.Event) -> None:
    """Ролик по кругу в реальном темпе; в бэкенд уходит send_fps кадров в секунду.

    Пришёл запрос на ролик с нарушением — играем его один раз (события уходят в нужные секунды),
    потом возвращаемся к основному ролику.
    """
    cap = cv2.VideoCapture(str(cam.path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    with cam.lock:
        cam.active_boxes = cam.boxes
    clip: Clip | None = None
    clip_track = 0
    fired: set[int] = set()
    next_frame = time.monotonic()
    last_sent = 0.0
    while not stop.is_set():
        with cam.lock:
            wanted, cam.clip_request = cam.clip_request, None
        if wanted is not None:
            cap.release()
            cap, clip, fired = cv2.VideoCapture(str(wanted.path)), wanted, set()
            clip_track = next(_track_ids)
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            with cam.lock:
                cam.active_boxes = wanted.boxes
            next_frame = time.monotonic()
        ok, frame = cap.read()
        if not ok:
            cap.release()
            cap = cv2.VideoCapture(str(cam.path))  # конец ролика: основной с начала
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            clip = None
            with cam.lock:
                cam.active_boxes = cam.boxes
            next_frame = time.monotonic()
            continue
        h, w = frame.shape[:2]
        frame = cv2.resize(frame, (width, int(h * width / w)))
        with cam.lock:
            cam.frame = frame
            cam.pos_s = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if clip is not None:
            for i, (at, kind) in enumerate(clip.events):
                if i not in fired and cam.pos_s >= at:
                    fired.add(i)
                    threading.Thread(
                        target=safe_send,
                        args=(cam, backend, kind, clip_track),
                        daemon=True,
                    ).start()
        now = time.monotonic()
        if now - last_sent >= 1.0 / send_fps:
            last_sent = now
            try:
                request(
                    backend,
                    f"/api/cameras/{cam.camera_id}/frame",
                    jpeg(cam.preview(frame)),
                    "image/jpeg",
                )
            except (urllib.error.URLError, OSError):
                pass  # бэкенд ещё поднимается или перезапускается — кадр пропускаем
        next_frame += 1.0 / fps
        stop.wait(max(0.0, next_frame - time.monotonic()))
    cap.release()


def snapshot(cam: CamState, box: list[float]) -> bytes:
    with cam.lock:
        frame = None if cam.frame is None else cam.frame.copy()
    if frame is None:
        return b""
    h, w = frame.shape[:2]
    p1, p2 = (int(box[0] * w), int(box[1] * h)), (int(box[2] * w), int(box[3] * h))
    cv2.rectangle(frame, p1, p2, (95, 90, 255), 3)
    return jpeg(frame, 82)


def send_event(cam: CamState, backend: str, kind: str, track_id: int) -> None:
    box = cam.person_box()
    cam.flag()
    url = None
    shot = snapshot(cam, box)
    if shot:
        url = json.loads(request(backend, "/api/snapshots", shot, "image/jpeg")).get("url")
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    event = {
        "camera_id": cam.camera_id,
        "ts": now,
        "frame_id": next(_frame_ids),
        "type": kind,
        "bbox": box,
        "confidence": round(random.uniform(0.72, 0.93), 2),
        "track_id": track_id,
        "severity": SEVERITY.get(kind, "high"),
    }
    if url:
        event["snapshot_url"] = url
    request(backend, "/api/events", json.dumps(event).encode("utf-8"), "application/json")
    print(f"-> {kind} {cam.camera_id}", flush=True)


def safe_send(cam: CamState, backend: str, kind: str, track_id: int) -> None:
    try:
        send_event(cam, backend, kind, track_id)
    except (urllib.error.URLError, OSError) as err:
        print(f"бэкенд недоступен: {err}", file=sys.stderr, flush=True)


def scenario(
    cams: dict[str, CamState],
    clips: dict[str, Clip],
    backend: str,
    args: argparse.Namespace,
    stop: threading.Event,
) -> None:
    """Нарушения по одному: пауза в начале, потом случайная между ними; повтор — тот же track_id."""
    if stop.wait(args.first):
        return
    step = 0
    while not stop.is_set():
        camera_id, what, repeats = SCENARIO[step % len(SCENARIO)]
        step += 1
        cam = cams.get(camera_id)
        clip = clips.get(what.removeprefix("clip:")) if what.startswith("clip:") else None
        if cam is not None and clip is not None:
            with cam.lock:
                cam.clip_request = clip
        elif cam is not None and not what.startswith("clip:"):
            track_id = next(_track_ids)
            for i in range(repeats + 1):
                if i and stop.wait(REPEAT_PAUSE_S):
                    return
                safe_send(cam, backend, what, track_id)
        else:
            continue  # нет камеры или ролика — шаг пропускаем без паузы
        if stop.wait(random.uniform(args.gap_min, args.gap_max)):
            return


def load_clips(boxes: dict, folder: Path) -> dict[str, Clip]:
    found = {}
    for name, events in CLIPS.items():
        path = folder / f"{name}.mp4"
        if path.exists():
            found[name] = Clip(name, path, events, boxes.get(name, {}).get("boxes", []))
    return found


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Демо: ролики по кругу + редкие нарушения")
    parser.add_argument("--backend", default=os.environ.get("BACKEND_URL", "http://localhost:8080"))
    parser.add_argument(
        "--cam", action="append", default=[], metavar="ID=ФАЙЛ", help="камера=видеофайл"
    )
    parser.add_argument("--first", type=float, default=10.0, help="пауза до первого нарушения, с")
    parser.add_argument(
        "--gap-min", type=float, default=12.0, help="мин. пауза между нарушениями, с"
    )
    parser.add_argument(
        "--gap-max", type=float, default=20.0, help="макс. пауза между нарушениями, с"
    )
    parser.add_argument("--fps", type=float, default=8.0, help="кадров превью в секунду на камеру")
    parser.add_argument("--width", type=int, default=960, help="ширина кадра превью, px")
    args = parser.parse_args(argv)

    boxes = json.loads(BOXES_FILE.read_text(encoding="utf-8")) if BOXES_FILE.exists() else {}
    cams: dict[str, CamState] = {}
    for item in args.cam:
        camera_id, _, path = item.partition("=")
        if not path or not Path(path).exists():
            print(f"нет видео для {camera_id}: {path or item}", file=sys.stderr)
            return 1
        cams[camera_id] = CamState(camera_id, Path(path), boxes.get(camera_id, {}).get("boxes", []))
    if not cams:
        print("нужен хотя бы один --cam ID=ФАЙЛ", file=sys.stderr)
        return 1
    clips = load_clips(boxes, next(iter(cams.values())).path.parent)

    stop = threading.Event()
    threads = [
        threading.Thread(
            target=stream, args=(cam, args.backend, args.fps, args.width, stop), daemon=True
        )
        for cam in cams.values()
    ]
    threads.append(
        threading.Thread(target=scenario, args=(cams, clips, args.backend, args, stop), daemon=True)
    )
    for thread in threads:
        thread.start()
    print(
        f"видео по кругу: {', '.join(cams)}; роликов с нарушениями: {len(clips)}; "
        f"первое нарушение через {args.first:.0f} с",
        flush=True,
    )
    try:
        while not stop.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
