"""Проверка новых паттернов на роликах: найдено, пропущено, ложные, дубли по типу.

События воркера (JSON по контракту) против разметки (labels.json). Дубли без нарушения -
ролики, где нарушения нет совсем (--negative камера...): любое событие паттерна там ложное.
Статус паттерна: нет ни разметки, ни роликов - «проверено на синтетике»;
есть пропуски или ложные - «не готов»; иначе - «на роликах без замечаний».

Запуск из папки ml/ (воркер прогнан по роликам, события собраны в events.json):
py -m eval.patterns events.json labels.json [--negative cam ...] [--tol 1.0]
Оценка на роликах команды и открытых роликах, не с площадки.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval.evaluate import evaluate_full, parse_ts

PATTERNS = (
    "under_load",
    "distress_signal",
    "at_height",
    "loitering",
    "lone_worker",
    "overcrowding",
    "phone",
    "fence_crossing",
)
SYNTH = "проверено на синтетике"
NOT_READY = "не готов"
OK = "на роликах без замечаний"


def pattern_stats(
    events: list[dict],
    labels: list[dict],
    negative: tuple[str, ...] = (),
    tol: float = 1.0,
    start=None,
) -> dict[str, dict]:
    """По каждому паттерну: found, missed, false, dups, neg_false (на роликах без нарушения)."""
    if set(negative) & {lab["camera"] for lab in labels}:
        raise ValueError("негативный ролик содержит размеченные нарушения")
    scored = evaluate_full(events, labels, tol, start)
    out = {}
    for typ in PATTERNS:
        labs = [lab for lab in labels if lab["type"] == typ]
        row = scored.get(typ, {"found": 0, "missed": 0, "false": 0, "dup": 0})
        neg_false = sum(e["type"] == typ and e["camera_id"] in negative for e in events)
        r = {
            "found": row["found"],
            "missed": row["missed"],
            "false": row["false"] - neg_false,
            "dups": row["dup"],
            "neg_false": neg_false,
        }
        r["videos"] = len({lab["camera"] for lab in labs} | set(negative)) if labs else 0
        r["status"] = status(r, bool(labs))
        out[typ] = r
    return out


def status(r: dict, has_data: bool) -> str:
    if r["neg_false"] or r["false"]:
        return NOT_READY
    if not has_data:
        return SYNTH
    if r["missed"] or r["false"] or r["neg_false"] or r["dups"]:
        return NOT_READY
    return OK


def format_table(stats: dict[str, dict]) -> str:
    head = (
        "| тип | найдено | пропущено | ложные | дубли | ложные на роликах без нарушения | статус |"
    )
    rows = [head, "|---|---|---|---|---|---|---|"]
    for typ, r in stats.items():
        if r["status"] == SYNTH:
            cells = ["-"] * 5
        else:
            cells = [str(r[k]) for k in ("found", "missed", "false", "dups", "neg_false")]
        rows.append(f"| `{typ}` | " + " | ".join(cells) + f" | {r['status']} |")
    return "\n".join(rows)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("events")
    p.add_argument("labels")
    p.add_argument("--negative", nargs="*", default=[], help="камеры (ролики) без нарушения")
    p.add_argument("--tol", type=float, default=1.0)
    p.add_argument("--start")
    a = p.parse_args()
    events = json.loads(Path(a.events).read_text(encoding="utf-8"))
    labels = json.loads(Path(a.labels).read_text(encoding="utf-8"))
    start = parse_ts(a.start) if a.start else None
    print(format_table(pattern_stats(events, labels, tuple(a.negative), a.tol, start)))


if __name__ == "__main__":
    main()
