"""Подбор порогов: conf, N кадров подряд и cooldown по размеченным событиям.

Вход — журнал кадров (JSON-список; воркер/прогон пишет по кадру кандидатов на нарушение):
{"camera": "cam-1", "t": 3.2, "detections": [{"type": "no_helmet", "track_id": 4, "conf": 0.61}]}
и разметка как у eval.evaluate. Для каждой комбинации заново прогоняется
антидребезг (worker.rules.Debouncer) и считаются найдено / пропущено / ложные.
Лучшая — минимум пропусков при ложных не больше --max-false (по умолчанию 0), затем меньше
ложных, затем строже пороги. Если ни одна комбинация в ограничение не укладывается, первой идёт
с наименьшим числом ложных.

Запуск из папки ml/:
py -m eval.tune frames.json labels.json [--conf 0.3,0.4,0.5] [--frames-n 5,10] [--cooldown 5,inf]
    [--max-false 0]
"""

from __future__ import annotations

import argparse
import itertools
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from worker.rules import Debouncer

from .evaluate import evaluate

EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def replay(frames: list[dict], conf: float, min_frames: int, cooldown_s: float) -> list[dict]:
    """Прогнать журнал кадров через порог и антидребезг; вернуть события."""
    debouncers: dict[str, Debouncer] = {}
    seen: dict[str, set[tuple[int, str]]] = {}
    events = []
    for fr in sorted(frames, key=lambda f: (f["camera"], f["t"])):
        cam, t = fr["camera"], fr["t"]
        deb = debouncers.setdefault(cam, Debouncer(min_frames, cooldown_s))
        keys = seen.setdefault(cam, set())
        active = {
            (d["track_id"], d["type"]): d["conf"] for d in fr["detections"] if d["conf"] >= conf
        }
        keys.update(active)
        for key in sorted(keys):
            if deb.update(key[0], key[1], key in active, t):
                events.append(
                    {
                        "camera_id": cam,
                        "type": key[1],
                        "track_id": key[0],
                        "ts": (EPOCH + timedelta(seconds=t)).isoformat(),
                    }
                )
    return events


def tune(
    frames: list[dict],
    labels: list[dict],
    confs: list[float],
    min_frames: list[int],
    cooldowns: list[float],
    tol: float = 1.0,
    max_false: int = 0,
) -> list[dict]:
    """Строки таблицы, лучшая первой: минимум пропусков при ложных <= max_false."""
    rows = []
    for c, n, cd in itertools.product(confs, min_frames, cooldowns):
        events = replay(frames, c, n, cd)
        res = evaluate(events, labels, tol, EPOCH)
        found = sum(r["found"] for r in res.values())
        missed = sum(r["missed"] for r in res.values())
        false = sum(r["false"] for r in res.values())
        rows.append(
            {
                "conf": c,
                "frames": n,
                "cooldown": cd,
                "found": found,
                "missed": missed,
                "false": false,
            }
        )
    rows.sort(key=lambda r: sort_key(r, max_false))
    return rows


def sort_key(r: dict, max_false: int) -> tuple:
    if r["false"] <= max_false:
        return (0, r["missed"], r["false"], -r["conf"], -r["frames"])
    return (1, r["false"], r["missed"], -r["conf"], -r["frames"])


def format_table(rows: list[dict]) -> str:
    head = f"{'conf':>6}{'кадров':>8}{'cooldown':>10}{'найдено':>9}{'пропущено':>11}{'ложные':>8}"
    lines = [head, *(_line(r) for r in rows)]
    if rows:
        lines += ["", "Лучшая:", head, _line(rows[0])]
    return "\n".join(lines)


def _line(r: dict) -> str:
    return (
        f"{r['conf']:>6}{r['frames']:>8}{r['cooldown']:>10}"
        f"{r['found']:>9}{r['missed']:>11}{r['false']:>8}"
    )


def _floats(s: str) -> list[float]:
    return [float(x) for x in s.split(",")]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("frames")
    p.add_argument("labels")
    p.add_argument("--conf", default="0.25,0.3,0.35,0.4,0.5,0.6")
    p.add_argument("--frames-n", dest="n", default="3,5,10,15,20")
    p.add_argument("--cooldown", default="5,15,inf")
    p.add_argument("--tol", type=float, default=1.0)
    p.add_argument("--max-false", type=int, default=0, help="предел ложных событий")
    a = p.parse_args()
    frames = json.loads(Path(a.frames).read_text(encoding="utf-8"))
    labels = json.loads(Path(a.labels).read_text(encoding="utf-8"))
    rows = tune(
        frames,
        labels,
        _floats(a.conf),
        [int(x) for x in a.n.split(",")],
        _floats(a.cooldown),
        a.tol,
        a.max_false,
    )
    print(format_table(rows))


if __name__ == "__main__":
    main()
