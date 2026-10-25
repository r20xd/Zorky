"""EventIn в openapi.yaml и events.schema.json — одно событие; проверяем, что не разъехались."""

import json
from pathlib import Path

import yaml

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


def load() -> tuple[dict, dict]:
    schema = json.loads((CONTRACTS / "events.schema.json").read_text(encoding="utf-8"))
    openapi = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))
    return schema, openapi["components"]["schemas"]


def resolve(node: dict, components: dict) -> dict:
    ref = node.get("$ref")
    return components[ref.rsplit("/", 1)[-1]] if ref else node


def test_event_in_matches_json_schema():
    schema, components = load()
    event_in = components["EventIn"]
    assert set(event_in["required"]) == set(schema["required"])
    assert set(event_in["properties"]) == set(schema["properties"])
    violation_type = resolve(event_in["properties"]["type"], components)
    assert violation_type["enum"] == schema["properties"]["type"]["enum"]


def test_event_extends_event_in():
    _, components = load()
    event, event_in = components["Event"], components["EventIn"]
    assert set(event_in["properties"]) <= set(event["properties"])
    assert set(event_in["required"]) <= set(event["required"])
