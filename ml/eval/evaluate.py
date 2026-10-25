"""Оценка качества на роликах: события воркера против ручной разметки.

События — JSON-список или JSONL (по строке на событие) в формате контракта. Разметка — JSON-список
{"camera": "cam-1", "type": "no_helmet", "start_s": 3.0, "end_s": 9.5}
(секунды от начала ролика). Время события = ts - начало ролика; начало по умолчанию —
2000-01-01T00:00:00Z, как у воркера `--offline` (ts = начало + кадр / FPS), можно задать --start.

По типам: найдено, пропущено, ложные, дубли (больше одного события на интервал метки),
задержка от начала нарушения до первого события. Ложные на час видео без нарушений —
по длительностям роликов (--durations durations.json, их пишет воркер рядом с журналом).

Запуск из папки ml/:
py -m eval.evaluate events.jsonl labels.json [--tol 1.0] [--start ISO] [--durations d.json]
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from worker.offline import OFFLINE_START


def parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def event_times(events: list[dict], start: datetime | None = None) -> list[float]:
    """Секунды от начала ролика для каждого события (порядок сохраняется)."""
    start = start or OFFLINE_START
    return [(parse_ts(e["ts"]) - start).total_seconds() for e in events]


def load_events(path: str | Path) -> list[dict]:
    """JSON-список или JSONL."""
    text = Path(path).read_text(encoding="utf-8")
    if text.lstrip().startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def evaluate(
    events: list[dict],
    labels: list[dict],
    tol: float = 1.0,
    start: datetime | None = None,
) -> dict[str, dict[str, int]]:
    """По типам: found / missed / false. Событие попадает в окно [start_s-tol, end_s+tol]."""
    return {
        typ: {key: row[key] for key in ("found", "missed", "false")}
        for typ, row in evaluate_full(events, labels, tol, start).items()
    }


def _window(lab: dict, e: dict, t: float, tol: float) -> bool:
    return (
        e["camera_id"] == lab["camera"]
        and e["type"] == lab["type"]
        and lab["start_s"] - tol <= t <= lab["end_s"] + tol
        and all(lab[key] == e.get(key) for key in ("track_id", "zone_id") if key in lab)
    )


def _match_episodes(events, labels, times, tol):
    """Максимальное паросочетание: событие подтверждает не более одного эпизода."""
    order = sorted(range(len(events)), key=lambda i: (times[i], i))
    candidates = [[i for i in order if _window(lab, events[i], times[i], tol)] for lab in labels]
    owner, assigned = {}, {}
    for root in sorted(range(len(labels)), key=lambda j: (labels[j]["end_s"], j)):
        pending, previous, visited = [root], {}, {root}
        free = None
        for lab_id in pending:
            for event_id in candidates[lab_id]:
                if event_id in previous:
                    continue
                previous[event_id] = lab_id
                if event_id not in owner:
                    free = event_id
                    break
                other = owner[event_id]
                if other not in visited:
                    visited.add(other)
                    pending.append(other)
            if free is not None:
                break
        while free is not None:
            lab_id = previous[free]
            old = assigned.get(lab_id)
            assigned[lab_id], owner[free] = free, lab_id
            free = old
    return assigned, set(owner), candidates


def evaluate_full(
    events: list[dict],
    labels: list[dict],
    tol: float = 1.0,
    start: datetime | None = None,
) -> dict[str, dict]:
    """По типам: found / missed / false / dup / delays (секунды от начала метки до первого события).

    dup — события сверх первого внутри одной метки."""
    if not math.isfinite(tol) or tol < 0:
        raise ValueError("tol должен быть конечным числом >= 0")
    for lab in labels:
        if not all(math.isfinite(lab[k]) for k in ("start_s", "end_s")):
            raise ValueError("интервалы разметки должны быть конечными")
        if lab["end_s"] < lab["start_s"]:
            raise ValueError("конец эпизода раньше начала")
    res = defaultdict(lambda: {"found": 0, "missed": 0, "false": 0, "dup": 0, "delays": []})
    times = event_times(events, start)
    assigned, used, candidates = _match_episodes(events, labels, times, tol)
    for j, lab in enumerate(labels):
        row = res[lab["type"]]
        row["found" if j in assigned else "missed"] += 1
        if j in assigned:
            row["delays"].append(round(times[assigned[j]] - lab["start_s"], 3))
    covered = {i for group in candidates for i in group}
    for i, event in enumerate(events):
        if i not in used:
            res[event["type"]]["dup" if i in covered else "false"] += 1
    return dict(sorted(res.items()))


def percentile(values: list[float], q: float) -> float | None:
    """Линейная интерполяция; пустая выборка не становится нулевой задержкой."""
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    lo, hi = math.floor(index), math.ceil(index)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo), 3)


def metric_summary(result: dict[str, dict]) -> dict[str, dict]:
    """Качество оповещений; дубли штрафуются как лишние сообщения.

    Задержка — до события воркера, не до экрана. Ранние совпадения в пределах
    tol считаются отдельно; для перцентилей их задержка равна нулю.
    """
    out = {}
    for typ, row in result.items():
        tp, fn, fp, dup = (row[k] for k in ("found", "missed", "false", "dup"))
        predicted, actual = tp + fp + dup, tp + fn
        delays = [max(0.0, value) for value in row["delays"]]
        out[typ] = {
            **row,
            "precision": tp / predicted if predicted else None,
            "recall": tp / actual if actual else None,
            "f1": 2 * tp / (predicted + actual) if predicted + actual else None,
            "duplicate_rate": dup / predicted if predicted else None,
            "delay_samples": len(delays),
            "early_matches": sum(value < 0 for value in row["delays"]),
            "delay_p50_s": percentile(delays, 0.5),
            "delay_p95_s": percentile(delays, 0.95),
            "delay_max_s": max(delays) if delays else None,
        }
    return out


def clean_false_per_hour(
    events: list[dict],
    labels: list[dict],
    durations: dict[str, float],
    tol: float = 1.0,
    start: datetime | None = None,
    negative_cameras: list[str] | None = None,
) -> dict:
    """Ложные на роликах без меток: число, часы видео, на час (None - длительностей нет)."""
    labeled = {lab["camera"] for lab in labels}
    if negative_cameras is not None:
        missing = set(negative_cameras) - set(durations)
        if missing or set(negative_cameras) & labeled:
            raise ValueError("негативные камеры должны иметь длительность и не иметь нарушений")
    clean = {
        c: d
        for c, d in durations.items()
        if c not in labeled and (negative_cameras is None or c in negative_cameras)
    }
    if any(not math.isfinite(d) or d < 0 for d in clean.values()):
        raise ValueError("длительность должна быть конечным числом >= 0")
    hours = sum(clean.values()) / 3600
    n = sum(1 for e in events if e["camera_id"] in clean)
    return {
        "false": n,
        "hours": hours,
        "per_hour": n / hours if hours > 0 else None,
        "negative_scope": "explicit" if negative_cameras is not None else "assumed_unlabeled",
        "zero_count_upper95_per_hour": -math.log(0.05) / hours if n == 0 and hours > 0 else None,
    }


def format_full(res: dict[str, dict], clean: dict | None = None) -> str:
    head = (
        f"{'тип':<18}{'найдено':>9}{'пропущено':>11}{'ложные':>8}{'дубли':>7}"
        f"{'задержка ср, с':>16}{'макс, с':>9}"
    )
    lines = [head]
    total = {"found": 0, "missed": 0, "false": 0, "dup": 0}
    for typ, r in res.items():
        d = [max(0.0, value) for value in r["delays"]]
        mean = f"{sum(d) / len(d):.1f}" if d else "-"
        mx = f"{max(d):.1f}" if d else "-"
        lines.append(
            f"{typ:<18}{r['found']:>9}{r['missed']:>11}{r['false']:>8}{r['dup']:>7}"
            f"{mean:>16}{mx:>9}"
        )
        for k in total:
            total[k] += r[k]
    lines.append(
        f"{'всего':<18}{total['found']:>9}{total['missed']:>11}{total['false']:>8}{total['dup']:>7}"
    )
    if clean is not None:
        rate = "-" if clean["per_hour"] is None else f"{clean['per_hour']:.1f}"
        lines.append(
            f"ролики без нарушений: ложных {clean['false']} на {clean['hours']:.3f} ч, "
            f"на час видео: {rate}"
        )
    return "\n".join(lines)


def format_report(result: dict[str, dict[str, int]]) -> str:
    lines = [f"{'тип':<15}{'найдено':>9}{'пропущено':>11}{'ложные':>8}"]
    total = {"found": 0, "missed": 0, "false": 0}
    for typ, r in result.items():
        lines.append(f"{typ:<15}{r['found']:>9}{r['missed']:>11}{r['false']:>8}")
        for k in total:
            total[k] += r[k]
    lines.append(f"{'всего':<15}{total['found']:>9}{total['missed']:>11}{total['false']:>8}")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("events")
    p.add_argument("labels")
    p.add_argument("--tol", type=float, default=1.0, help="допуск по времени, с")
    p.add_argument("--start", help="ts начала ролика (ISO), по умолчанию 2000-01-01T00:00:00Z")
    p.add_argument("--durations", help="JSON {камера: секунды}: ложные на час видео без нарушений")
    p.add_argument("--json-out", type=Path, help="сохранить P/R/F1, дубли и p50/p95 задержки")
    p.add_argument(
        "--negative-camera", action="append", help="проверенный негативный ролик; можно повторять"
    )
    a = p.parse_args()
    events = load_events(a.events)
    labels = json.loads(Path(a.labels).read_text(encoding="utf-8"))
    start = parse_ts(a.start) if a.start else None
    clean = None
    if a.durations:
        durations = json.loads(Path(a.durations).read_text(encoding="utf-8"))
        clean = clean_false_per_hour(events, labels, durations, a.tol, start, a.negative_camera)
    elif a.negative_camera:
        p.error("--negative-camera требует --durations")
    result = evaluate_full(events, labels, a.tol, start)
    print(format_full(result, clean))
    if a.json_out:
        report = {
            "matching": "one-to-one camera/type/time; optional track_id/zone_id",
            "tolerance_s": a.tol,
            "delay_scope": "episode onset to worker event; not end-to-end notification",
            "by_type": metric_summary(result),
            "negative_clips": clean,
        }
        a.json_out.parent.mkdir(parents=True, exist_ok=True)
        a.json_out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
