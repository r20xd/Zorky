"""Сборка события нарушения по контракту contracts/events.schema.json."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "events.schema.json"

ViolationType = Literal[
    "no_helmet",
    "no_respirator",
    "danger_zone",
    "smoking",
    "fall",
    "immobile",
    "vehicle_proximity",
    "running",
    "phone",
    "lone_worker",
    "overcrowding",
]


def iso_utc(moment: datetime) -> str:
    """ISO 8601 в UTC с миллисекундами и суффиксом Z — так ts ждёт контракт."""
    text = moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    return text.replace("+00:00", "Z")


def make_event(
    *,
    camera_id: str,
    frame_id: int,
    violation: ViolationType,
    bbox: Sequence[float],
    confidence: float,
    track_id: int | None,
    ts: datetime | None = None,
    snapshot_url: str | None = None,
    zone_id: str | None = None,
    severity: str | None = None,
) -> dict:
    """Тело для POST /api/events. bbox — [x1, y1, x2, y2], нормированные 0..1."""
    event = {
        "camera_id": camera_id,
        "ts": iso_utc(ts or datetime.now(timezone.utc)),
        "frame_id": frame_id,
        "type": violation,
        "bbox": [round(min(max(v, 0.0), 1.0), 4) for v in bbox],
        "confidence": round(confidence, 3),
    }
    if track_id is not None:
        event["track_id"] = track_id
    if snapshot_url:
        event["snapshot_url"] = snapshot_url
    if severity is not None:
        event["severity"] = severity
    if zone_id:
        event["zone_id"] = zone_id
    return event


@lru_cache(maxsize=1)
def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_event(event: dict) -> None:
    """Бросает jsonschema.ValidationError, если событие не по контракту.

    jsonschema есть только в dev-зависимостях: вызывается из тестов, а не в горячем цикле воркера.
    """
    import jsonschema

    validator = jsonschema.Draft202012Validator(
        load_schema(), format_checker=jsonschema.FormatChecker()
    )
    validator.validate(event)
