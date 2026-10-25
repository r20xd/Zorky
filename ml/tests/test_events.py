import json
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest

from worker.events import make_event, validate_event

SAMPLE = Path(__file__).resolve().parents[2] / "demo" / "events.sample.json"


def event(**overrides) -> dict:
    fields = {
        "camera_id": "cam-1",
        "frame_id": 0,
        "violation": "no_helmet",
        "bbox": (0.1, 0.2, 0.3, 0.4),
        "confidence": 0.5,
        "track_id": 1,
    }
    return make_event(**{**fields, **overrides})


def test_sample_events_match_contract():
    events = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert {e["type"] for e in events} == {"no_helmet", "no_respirator", "danger_zone", "smoking"}
    for sample in events:
        validate_event(sample)


def test_make_event_matches_contract():
    built = event(
        violation="danger_zone",
        confidence=0.91234,
        ts=datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc),
        zone_id="z-1",
    )
    validate_event(built)
    assert built["ts"] == "2026-10-21T10:00:00.000Z"
    assert built["confidence"] == 0.912
    assert built["zone_id"] == "z-1"


def test_bbox_is_clamped_to_frame():
    built = event(bbox=(-0.01, 0.2, 1.02, 0.9))
    assert built["bbox"] == [0.0, 0.2, 1.0, 0.9]
    validate_event(built)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", "no_gloves"),
        ("confidence", 1.5),
        ("bbox", [0.1, 0.2, 0.3]),
        ("camera_id", ""),
        ("track_id", -1),
    ],
)
def test_contract_rejects_bad_events(field, value):
    built = event()
    built[field] = value
    with pytest.raises(jsonschema.ValidationError):
        validate_event(built)


def test_contract_rejects_unknown_fields():
    built = event()
    built["camera"] = "cam-1"
    with pytest.raises(jsonschema.ValidationError):
        validate_event(built)
