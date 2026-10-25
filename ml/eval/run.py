"""Прогон воркера по роликам и оценка сценариев: найдено / пропущено / ложные / дубли.

Два шага. Детекции роликов считаются один раз и кладутся в кэш (ml/runs/eval-cache, вне git):
модель людей и модель СИЗ, как в воркере. Правила (worker.pipeline.find_violations с
Debouncer) гоняются по кэшу с временем кадра, поэтому подбор порогов rules.yaml
не требует повторного инференса.

Ролик без записи в разметке считается роликом без нарушений: любое его событие — ложное.
Дубль — второе и следующие события того же типа внутри одного размеченного эпизода.

Запуск из папки ml/:
py -m eval.run detect [--device cpu] [--ppe-weights runs/release-ppe/best.pt] [ролик ...]
py -m eval.run report [--rules worker/rules.yaml] [--tol 1.0]
py -m eval.run sweep running.speed_threshold=1.0,1.5,2.0 [--rules ...]
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

from worker.offline import OFFLINE_START, load_zones, replay_frames
from worker.rules import Detection, build_rules, load_config

from .evaluate import clean_false_per_hour, evaluate_full, format_full

ROOT = Path(__file__).resolve().parents[2]
VIDEOS = ROOT / "demo" / "videos"
LABELS = Path(__file__).with_name("labels.json")
CACHE = Path(__file__).resolve().parents[1] / "runs" / "eval-cache"
EPOCH = OFFLINE_START


def detection_to_dict(d: Detection) -> dict:
    return {
        "cls": d.cls,
        "bbox": list(d.bbox),
        "conf": d.confidence,
        "track": d.track_id,
        "aspect": d.frame_aspect,
    }


def detection_from_dict(d: dict) -> Detection:
    return Detection(d["cls"], tuple(d["bbox"]), d["conf"], d["track"], frame_aspect=d["aspect"])


def detect_video(video: Path, people, ppe, max_frames: int | None = None) -> dict:
    """Кадры ролика -> {"fps", "frames": [{"t", "detections"}]}; t = номер кадра / fps."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frames = []
    i = 0
    while max_frames is None or i < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        found = list(people(frame))
        if ppe is not None:
            found += ppe(frame)
        frames.append({"t": i / fps, "detections": [detection_to_dict(d) for d in found]})
        i += 1
    cap.release()
    return {"fps": fps, "frames": frames}


# Эти правила в обычных роликах дали бы «ложные» на людях, которых никто не размечал
# (без каски; один человек в кадре; толпа): работают только на камерах, где в разметке есть их тип.
PPE_RULES = ("no_helmet", "no_respirator", "smoking", "lone_worker", "overcrowding")


def replay_rules(
    frames: list[dict], camera: str, config: dict, labeled: frozenset[str] = frozenset()
) -> list[dict]:
    """Кэш детекций -> события контракта через worker.offline.replay_frames (та же логика, что в
    воркере --offline). Зоны ролика — eval/zones/<камера>.json; без файла правила с зонами молчат.

    labeled — типы, размеченные на этой камере (открывают СИЗ-правила)."""
    rules = [r for r in build_rules(camera, config) if r.name not in PPE_RULES or r.name in labeled]
    items = (
        (i, fr["t"], [detection_from_dict(d) for d in fr["detections"]])
        for i, fr in enumerate(frames)
    )
    return list(replay_frames(items, camera, rules, load_zones(camera), EPOCH))


def score(events: list[dict], labels: list[dict], tol: float = 1.0) -> dict[str, dict[str, int]]:
    """По типам: found / missed / false / dup. Типы без меток тоже попадают (все события ложные)."""
    res = evaluate_full(events, labels, tol, EPOCH)
    return {
        t: {k: r[k] for k in ("found", "missed", "false", "dup")} for t, r in sorted(res.items())
    }


def format_score(res: dict[str, dict[str, int]]) -> str:
    keys = ("found", "missed", "false", "dup")
    lines = [f"{'тип':<18}{'найдено':>9}{'пропущено':>11}{'ложные':>8}{'дубли':>7}"]
    total = dict.fromkeys(keys, 0)
    for typ, r in res.items():
        lines.append(
            f"{typ:<18}" + "".join(f"{r[k]:>{w}}" for k, w in zip(keys, (9, 11, 8, 7), strict=True))
        )
        for k in keys:
            total[k] += r[k]
    lines.append(
        f"{'всего':<18}"
        + "".join(f"{total[k]:>{w}}" for k, w in zip(keys, (9, 11, 8, 7), strict=True))
    )
    return "\n".join(lines)


def pick(name: str, only: str | None = None, skip: str | None = None) -> bool:
    """Фильтр роликов по подстрокам через запятую: only - оставить подходящие, skip - убрать."""
    if only and not any(x in name for x in only.split(",")):
        return False
    return not (skip and any(x in name for x in skip.split(",")))


def load_cache(
    cache: Path = CACHE, only: str | None = None, skip: str | None = None
) -> dict[str, list[dict]]:
    return {
        p.stem: json.loads(p.read_text(encoding="utf-8"))["frames"]
        for p in sorted(cache.glob("*.json"))
        if pick(p.stem, only, skip)
    }


def load_durations(
    cache: Path = CACHE, only: str | None = None, skip: str | None = None
) -> dict[str, float]:
    out = {}
    for p in sorted(cache.glob("*.json")):
        if not pick(p.stem, only, skip):
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        n = len(data["frames"])
        out[p.stem] = n / data["fps"] if data.get("fps") else 0.0
    return out


def run_all(cache: dict[str, list[dict]], config: dict, labels: list[dict]) -> list[dict]:
    events = []
    for camera, frames in cache.items():
        labeled = frozenset(x["type"] for x in labels if x["camera"] == camera)
        events += replay_rules(frames, camera, config, labeled)
    return events


def set_param(config: dict, rule: str, param: str, value: float) -> dict:
    """Копия config, где параметр правила задан во всех секциях (default и cameras)."""
    cfg = copy.deepcopy(config)
    sections = [cfg.get("default") or {}] + [c for c in (cfg.get("cameras") or {}).values()]
    for sec in sections:
        if rule in sec:
            sec[rule] = {**(sec[rule] or {}), param: value}
    return cfg


def sweep(cache, labels, config, rule: str, param: str, values: list[float], tol: float = 1.0):
    """Строки (значение, найдено, пропущено, ложные, дубли) только по типу rule."""
    rows = []
    for v in values:
        events = [
            e
            for e in run_all(cache, set_param(config, rule, param, v), labels)
            if e["type"] == rule
        ]
        labs = [lab for lab in labels if lab["type"] == rule]
        r = score(events, labs, tol).get(rule, {"found": 0, "missed": 0, "false": 0, "dup": 0})
        rows.append((v, r["found"], r["missed"], r["false"], r["dup"]))
    return rows


def format_sweep(rule: str, param: str, rows) -> str:
    lines = [f"{rule}.{param}: значение, найдено, пропущено, ложные, дубли"]
    lines += ["  " + ", ".join(str(x) for x in r) for r in rows]
    return "\n".join(lines)


def number(text: str) -> int | float:
    return int(text) if text.lstrip("-").isdigit() else float(text)


def cmd_detect(a) -> int:
    from worker.main import make_detectors

    CACHE.mkdir(parents=True, exist_ok=True)
    args = argparse.Namespace(
        model=a.model, pose_model=None, ppe_weights=a.ppe_weights, device=a.device
    )
    people_factory = lambda: make_detectors(args)  # noqa: E731
    names = a.videos or sorted(p.name for p in VIDEOS.glob("*.mp4"))
    for name in names:
        path = Path(name) if Path(name).exists() else VIDEOS / name
        out = CACHE / f"{path.stem}.json"
        if out.exists() and not a.force:
            continue
        people, ppe = people_factory()  # трекер у каждого ролика свой
        data = detect_video(path, people, ppe, a.max_frames)
        out.write_text(json.dumps(data), encoding="utf-8")
        print(f"{path.name}: {len(data['frames'])} кадров", flush=True)
    return 0


def cmd_report(a) -> int:
    cache = load_cache(only=a.only, skip=a.skip)
    if not cache:
        print("кэш пуст: сначала py -m eval.run detect", file=sys.stderr)
        return 1
    labels = [x for x in json.loads(LABELS.read_text(encoding="utf-8")) if x["camera"] in cache]
    config = load_config(a.rules)
    events = run_all(cache, config, labels)
    print(
        format_full(
            evaluate_full(events, labels, a.tol, EPOCH),
            clean_false_per_hour(
                events, labels, load_durations(only=a.only, skip=a.skip), a.tol, EPOCH
            ),
        )
    )
    return 0


def cmd_sweep(a) -> int:
    cache = load_cache(only=a.only, skip=a.skip)
    labels = [x for x in json.loads(LABELS.read_text(encoding="utf-8")) if x["camera"] in cache]
    config = load_config(a.rules)
    for item in a.items:
        key, vals = item.split("=")
        rule, param = key.split(".")
        rows = sweep(
            cache, labels, config, rule, param, [number(x) for x in vals.split(",")], a.tol
        )
        print(format_sweep(rule, param, rows))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detect")
    d.add_argument("videos", nargs="*")
    d.add_argument("--model", default=str(ROOT / "yolo11n.pt"))
    d.add_argument(
        "--ppe-weights", default=str(Path(__file__).parents[1] / "runs/release-ppe/best.pt")
    )
    d.add_argument("--device", choices=["cpu", "gpu"], default="cpu")
    d.add_argument("--max-frames", type=int)
    d.add_argument("--force", action="store_true")
    r = sub.add_parser("report")
    r.add_argument("--rules")
    r.add_argument("--tol", type=float, default=1.0)
    s = sub.add_parser("sweep")
    s.add_argument("items", nargs="+", help="правило.параметр=знач,знач")
    s.add_argument("--rules")
    s.add_argument("--tol", type=float, default=1.0)
    for q in (r, s):
        q.add_argument(
            "--only", help="только ролики, в имени которых есть подстрока (через запятую)"
        )
        q.add_argument("--skip", help="убрать ролики с подстрокой в имени (через запятую)")
    a = p.parse_args(argv)
    return {"detect": cmd_detect, "report": cmd_report, "sweep": cmd_sweep}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
