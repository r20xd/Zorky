"""Замеры на LinQ для ноутбука `linq_bench.ipynb` (удалённый Jupyter): чистые функции с тестами.

Ноутбук только вызывает их; те же функции тестируются с подменой pytpu.
Программы: `packages/<роль>.tpu` + `<роль>.tpu.json`, роли ppe, people, pose.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

import numpy as np

ROLES = ("ppe", "people", "pose")
STREAMS = (1, 2, 4)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv"}
CONF = 0.25
CAMERA_FPS = 5.0  # столько кадров/с на камеру достаточно для правил (для расчёта ёмкости)


def find_ml_dir(start: Path, unpack_to: Path | None = None) -> Path:
    """Папка ml/ (с пакетами worker и export): рядом с ноутбуком, выше или из ml.zip."""
    for base in (start, *start.parents):
        for cand in (base, base / "ml"):
            if (cand / "worker" / "infer.py").is_file() and (cand / "export").is_dir():
                return cand
    archive = next((p for p in (start / "ml.zip", start.parent / "ml.zip") if p.is_file()), None)
    if archive is None:
        raise FileNotFoundError(
            "нет папки ml/ и ml.zip: загрузите ml.zip (git archive -o ml.zip HEAD ml)"
        )
    import zipfile

    target = unpack_to or start / "_ml"
    with zipfile.ZipFile(archive) as z:
        z.extractall(target)
    return find_ml_dir(target)


def find_programs(folder: str | Path) -> dict[str, str]:
    """Роль -> путь к .tpu из папки; нужен парный `.tpu.json`. Роль — по имени файла."""
    found: dict[str, str] = {}
    for tpu in sorted(Path(folder).rglob("*.tpu")):
        if not Path(str(tpu) + ".json").is_file():
            continue
        role = next((r for r in ROLES if r in tpu.stem.lower()), None)
        if role and role not in found:
            found[role] = str(tpu)
    return found


def program_kind(role: str, description: dict) -> str:
    """detect | obb | pose: поле kind в .tpu.json, иначе по роли и имени."""
    kind = description.get("kind")
    if kind in ("detect", "obb", "pose"):
        return kind
    if role == "pose" or "pose" in str(description.get("name", "")).lower():
        return "pose"
    if "obb" in str(description.get("name", "")).lower():
        return "obb"
    return "detect"


def check_linq(api=None) -> list[str]:
    """Имена устройств LinQ; без pytpu или без устройств — DeviceUnavailable."""
    from worker.infer import DeviceUnavailable, load_linq_api

    api = api or load_linq_api()
    devices = api.Device.list_devices()
    if not devices:
        raise DeviceUnavailable("pytpu не видит ни одного устройства LinQ")
    return [str(d) for d in devices]


def frame_steps(session, kind: str, frame, imgsz: int = 640):
    """(пред, инференс, пост) для одной программы; разбор выхода по её виду."""
    from worker.detect import decode, decode_obb, decode_pose, letterbox

    decoders = {"detect": decode, "obb": decode_obb, "pose": decode_pose}
    decoder = decoders[kind]
    name = session.get_inputs()[0].name

    def pre():
        return letterbox(frame, imgsz)

    def infer(prepared):
        return session.run(None, {name: prepared[0]})[0]

    def post(output, prepared):
        return decoder(output, prepared[1], prepared[2], frame.shape[:2], CONF)

    return pre, infer, post


def combined_steps(steps: list):
    """Один кадр через все программы: общая предобработка, инференсы и разборы по очереди."""
    pre = steps[0][0]

    def infer(prepared):
        return [s[1](prepared) for s in steps]

    def post(outputs, prepared):
        return [s[2](o, prepared) for s, o in zip(steps, outputs, strict=True)]

    return pre, infer, post


def capacity(fps: float, camera_fps: float = CAMERA_FPS) -> int:
    """Сколько камер по camera_fps кадров/с тянет измеренный FPS."""
    return int(fps // camera_fps) if camera_fps > 0 else 0


def with_capacity(run: dict, camera_fps: float = CAMERA_FPS) -> dict:
    return {**run, "cameras": capacity(run["fps"], camera_fps), "camera_fps": camera_fps}


def bench_steps(steps, streams=STREAMS, frames: int = 50, camera_fps: float = CAMERA_FPS) -> list:
    from .bench import run_streams

    return [with_capacity(run_streams(steps, n, frames), camera_fps) for n in streams]


def bench_programs(
    programs: dict[str, str],
    frame,
    streams=STREAMS,
    frames: int = 50,
    api=None,
    camera_fps: float = CAMERA_FPS,
) -> dict:
    """Каждая программа отдельно и все вместе (СИЗ + люди + поза на кадр)."""
    from worker.infer import LinqSession

    sessions, steps = {}, {}
    for role, path in programs.items():
        sessions[role] = LinqSession(path, api=api)
        kind = program_kind(role, sessions[role].description)
        steps[role] = frame_steps(sessions[role], kind, frame)
    result = {
        "programs": {
            role: {"kind": program_kind(role, sessions[role].description), "path": programs[role]}
            for role in programs
        },
        "single": {role: bench_steps(s, streams, frames, camera_fps) for role, s in steps.items()},
        "combined": None,
        "combined_roles": [],
    }
    if len(steps) > 1:
        roles = [r for r in ROLES if r in steps]
        result["combined_roles"] = roles
        result["combined"] = bench_steps(
            combined_steps([steps[r] for r in roles]), streams, frames, camera_fps
        )
    return result


def table(result: dict) -> list[str]:
    """Markdown-таблица замеров: программа, потоки, FPS, p50/p95 инференса, пред, пост, камеры."""
    rows = [
        "| программа | потоки | FPS | инференс p50, мс | инференс p95, мс | пред p50, мс "
        "| пост p50, мс | камер |",
        "|---|---|---|---|---|---|---|---|",
    ]
    groups = list(result["single"].items())
    if result["combined"]:
        groups.append(("вместе: " + "+".join(result["combined_roles"]), result["combined"]))
    for name, runs in groups:
        for r in runs:
            rows.append(
                f"| {name} | {r['streams']} | {r['fps']:.1f} | {r['infer']['p50_ms']:.1f} "
                f"| {r['infer']['p95_ms']:.1f} | {r['pre']['p50_ms']:.1f} "
                f"| {r['post']['p50_ms']:.1f} | {r['cameras']} |"
            )
    return rows


# ---- INT8 (.tpu) против FP32 (.onnx) на срезе val СИЗ ----


def list_val(folder: str | Path, limit: int = 200) -> list[Path]:
    return sorted(p for p in Path(folder).rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)[:limit]


def read_labels(image: Path, shape: tuple[int, int]):
    """YOLO-разметка рядом (images -> labels, .txt): (xyxy в пикселях, классы) или None."""
    parts = list(image.parts)
    candidates = [image.with_suffix(".txt")]
    if "images" in parts:
        i = len(parts) - 1 - parts[::-1].index("images")
        candidates.insert(0, Path(*parts[:i], "labels", *parts[i + 1 :]).with_suffix(".txt"))
    path = next((c for c in candidates if c.is_file()), None)
    if path is None:
        return None
    height, width = shape
    boxes, classes = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        p = line.split()
        if len(p) < 5:
            continue
        cx, cy, w, h = (float(v) for v in p[1:5])
        boxes.append([(cx - w / 2) * width, (cy - h / 2) * height] + [0, 0])
        boxes[-1][2], boxes[-1][3] = (cx + w / 2) * width, (cy + h / 2) * height
        classes.append(int(p[0]))
    return np.array(boxes, dtype=float).reshape(-1, 4), np.array(classes, dtype=int)


def match(pred, truth, iou_thr: float = 0.5) -> int:
    """Число совпадений (тот же класс, IoU >= порога), жадно по убыванию оценки."""
    from worker.detect import iou_matrix

    (pb, ps, pc), (tb, tc) = pred, truth
    if not len(pb) or not len(tb):
        return 0
    ious = iou_matrix(np.asarray(pb, float), np.asarray(tb, float))
    used, hits = set(), 0
    for i in np.argsort(-np.asarray(ps)):
        best, best_iou = None, iou_thr
        for j in range(len(tb)):
            if j not in used and pc[i] == tc[j] and ious[i, j] >= best_iou:
                best, best_iou = j, ious[i, j]
        if best is not None:
            used.add(best)
            hits += 1
    return hits


def _prf(tp: int, n_pred: int, n_true: int) -> dict:
    precision = tp / n_pred if n_pred else 0.0
    recall = tp / n_true if n_true else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def compare_int8_fp32(
    int8_session,
    fp32_session,
    images: list[Path],
    load_image: Callable | None = None,
    imgsz: int = 640,
) -> dict:
    """INT8 (LinQ) и FP32 (onnxruntime) на одних кадрах: совпадение друг с другом и,
    если рядом есть разметка YOLO, точность/полнота каждой модели."""
    from worker.detect import decode, letterbox

    if load_image is None:
        import cv2

        load_image = lambda p: cv2.imread(str(p))  # noqa: E731
    counts = {k: 0 for k in ("frames", "int8", "fp32", "agree", "labeled", "truth")}
    counts.update(tp_int8=0, tp_fp32=0, pred_int8=0, pred_fp32=0)
    for path in images:
        frame = load_image(path)
        if frame is None:
            continue
        tensor, scale, pad = letterbox(frame, imgsz)
        found = {}
        for key, session in (("int8", int8_session), ("fp32", fp32_session)):
            out = session.run(None, {session.get_inputs()[0].name: tensor})[0]
            found[key] = decode(out, scale, pad, frame.shape[:2], CONF)
        counts["frames"] += 1
        counts["int8"] += len(found["int8"][0])
        counts["fp32"] += len(found["fp32"][0])
        counts["agree"] += match(found["int8"], (found["fp32"][0], found["fp32"][2]))
        truth = read_labels(path, frame.shape[:2])
        if truth is not None:
            counts["labeled"] += 1
            counts["truth"] += len(truth[0])
            for key in ("int8", "fp32"):
                counts[f"tp_{key}"] += match(found[key], truth)
                counts[f"pred_{key}"] += len(found[key][0])
    result = {
        "frames": counts["frames"],
        "detections": {"int8": counts["int8"], "fp32": counts["fp32"]},
        "agreement": _prf(counts["agree"], counts["int8"], counts["fp32"]),
        "labeled_frames": counts["labeled"],
    }
    if counts["labeled"]:
        for key in ("int8", "fp32"):
            result[key] = _prf(counts[f"tp_{key}"], counts[f"pred_{key}"], counts["truth"])
    return result


# ---- воркер на LinQ по роликам -> события JSONL ----


class FileClient:
    """Вместо бэкенда: события пишутся в JSONL; зон нет, снимки и превью отбрасываются."""

    def __init__(self, path: str | Path):
        import threading

        self.path, self.count = Path(path), 0
        self.path.write_text("", encoding="utf-8")
        self._lock = threading.Lock()

    def post_event(self, event: dict) -> dict:
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.count += 1
        return {}

    def get_zones(self, camera_id: str) -> list[dict]:
        return []

    def get_rules(self, camera_id):
        return {}

    def post_frame(self, camera_id: str, jpeg: bytes) -> None:
        return None

    def post_snapshot(self, jpeg: bytes) -> str:
        return ""

    def post_metrics(self, metrics: dict) -> None:
        return None


def list_videos(folder: str | Path) -> list[str]:
    return [str(p) for p in sorted(Path(folder).rglob("*")) if p.suffix.lower() in VIDEO_SUFFIXES]


def jsonl_to_array(jsonl: str | Path, out: str | Path) -> int:
    """JSONL -> JSON-массив: такой файл читает demo/replay_events.py --file."""
    events = [json.loads(x) for x in Path(jsonl).read_text(encoding="utf-8").splitlines() if x]
    Path(out).write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(events)


def run_worker(
    programs: dict[str, str],
    videos: list[str],
    out_jsonl: str | Path,
    max_frames: int | None = 300,
    cameras: list[str] | None = None,
) -> int:
    """Воркер (worker.main) на LinQ по роликам; события — в JSONL. Возвращает их число."""
    import argparse
    import threading

    from worker import main as wm

    if "people" not in programs:
        raise ValueError("для воркера нужна программа people")
    cameras = cameras or [f"cam-{i + 1}" for i in range(len(videos))]
    args = argparse.Namespace(
        device="linq",
        loop=False,
        max_frames=max_frames,
        fps=10.0,
    )
    client = FileClient(out_jsonl)
    from worker.infer import LinqSession

    sessions = {role: LinqSession(path) for role, path in programs.items()}
    lock = threading.Lock()
    jobs = []
    for camera_id, video in zip(cameras, videos, strict=True):
        # .tpu — не .onnx для make_detectors, поэтому детекторы собираются здесь на LinqSession
        people = wm.OnnxPeople(sessions["people"])
        if "pose" in sessions and wm.needs_pose(camera_id):
            people = wm.PosePeople(people, wm.OnnxPose(sessions["pose"]))
        ppe = wm.OnnxPpe(sessions["ppe"]) if "ppe" in sessions else None
        dropped = [lambda: 0]
        items = wm.video_frames(args, video, people, ppe, lock, dropped)
        jobs.append((camera_id, items, lambda d=dropped: d[0]()))
    wm.run_cameras(client, "linq", jobs)
    return client.count


# ---- Jupyter: логин и выгрузка результата ----


def jupyter_login(env=None, ask: Callable[[str], str] | None = None) -> tuple[str, str]:
    """(логин, пароль/токен) Jupyter: из LINQ_JUPYTER_USER/LINQ_JUPYTER_PASSWORD, иначе ввод."""
    env = os.environ if env is None else env
    user, secret = env.get("LINQ_JUPYTER_USER", ""), env.get("LINQ_JUPYTER_PASSWORD", "")
    if user and secret:
        return user, secret
    if ask is None:
        import getpass

        ask_user, ask_secret = input, getpass.getpass
    else:
        ask_user = ask_secret = ask
    return user or ask_user("Логин Jupyter: "), secret or ask_secret("Пароль/токен Jupyter: ")


def upload_result(url: str, remote_path: str, data: bytes, secret: str, opener=None) -> int:
    """PUT файла через Contents API Jupyter (заголовок Authorization: token ...)."""
    import base64

    body = json.dumps(
        {
            "type": "file",
            "format": "base64",
            "content": base64.b64encode(data).decode("ascii"),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{url.rstrip('/')}/api/contents/{remote_path}",
        data=body,
        method="PUT",
        headers={"Authorization": f"token {secret}", "Content-Type": "application/json"},
    )
    with (opener or urllib.request.urlopen)(request, timeout=30) as response:
        return response.status


def write_result(result: dict, path: str | Path) -> str:
    Path(path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def add_to_path(ml_dir: Path) -> None:
    if str(ml_dir) not in sys.path:
        sys.path.insert(0, str(ml_dir))
