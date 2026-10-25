"""Модель бэкенда принимает схему событий и demo/events.sample.json."""

import json
import re
from pathlib import Path
from typing import get_args

from fastapi.testclient import TestClient

from app.application.notifier import NopNotifier
from app.domain.models import Event, EventIn, ZoneKind
from app.infrastructure.memory_store import MemoryStore
from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]


def _schema() -> dict:
    return json.loads((ROOT / "contracts" / "events.schema.json").read_text(encoding="utf-8"))


def test_sample_events_parse():
    samples = json.loads((ROOT / "demo" / "events.sample.json").read_text(encoding="utf-8"))
    for sample in samples:
        EventIn.model_validate(sample)


def test_schema_fields_are_in_model():
    schema = _schema()
    assert set(schema["properties"]) <= set(EventIn.model_fields)
    for name in schema["required"]:
        assert EventIn.model_fields[name].is_required(), name


def test_backend_only_fields_are_optional_in_input():
    for name in ("severity", "incident_id", "count", "first_seen", "last_seen"):
        assert not EventIn.model_fields[name].is_required(), name
    assert "status_history" in Event.model_fields


def test_zone_kind_enum_covers_contract():
    text = (ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8")
    pattern = r"^ {4}Zone:\n.*?kind:\n\s+type: string\n\s+enum: \[([^\]]*)\]"
    match = re.search(pattern, text, re.S | re.M)
    assert match, "enum Zone.kind не найден в openapi.yaml"
    contract = {v.strip() for v in match.group(1).split(",")}
    assert contract <= set(get_args(ZoneKind))


def test_put_zones_accepts_machinery():
    client = TestClient(create_app(MemoryStore(), NopNotifier()))
    zones = [{"id": "z-1", "kind": "machinery", "polygon": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5]]}]
    assert client.put("/api/cameras/cam-9/zones", json=zones).status_code == 200


def test_put_zones_accepts_two_person():
    client = TestClient(create_app(MemoryStore(), NopNotifier()))
    zones = [{"id": "z-2", "kind": "two_person", "polygon": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5]]}]
    assert client.put("/api/cameras/cam-9/zones", json=zones).status_code == 200


def test_contract_camera_and_user_fields_are_in_model():
    from app.domain.models import Camera

    text = (ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8")
    match = re.search(r"^ {4}Camera:\n.*?properties:\n(.*?)\n\n", text, re.S | re.M)
    assert match, "Camera не найден в openapi.yaml"
    names = set(re.findall(r"^ {8}(\w+):\s*$", match.group(1), re.M))
    assert names <= set(Camera.model_fields)
    assert "area" in Camera.model_fields  # поле заранее, до контракта
