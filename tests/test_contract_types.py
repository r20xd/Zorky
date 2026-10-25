"""Контракт: реестр типов, схема события, openapi и demo/events.sample.json согласованы."""

import json
import re
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
C = ROOT / "contracts"
REG = json.loads((C / "violation_types.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((C / "events.schema.json").read_text(encoding="utf-8"))
API = yaml.safe_load((C / "openapi.yaml").read_text(encoding="utf-8"))
COMP = API["components"]["schemas"]
CODES = [t["code"] for t in REG["types"]]


def test_registry():
    assert {
        "no_helmet",
        "no_respirator",
        "danger_zone",
        "smoking",
        "fall",
        "immobile",
        "vehicle_proximity",
        "running",
        "phone",
        "fence_crossing",
        "under_load",
        "distress_signal",
        "lone_worker",
        "overcrowding",
        "at_height",
        "loitering",
        "vehicle_in_zone",
        "vehicle_collision",
    } <= set(CODES)
    assert len(CODES) == len(set(CODES))
    for t in REG["types"]:
        assert t["name"] and t["severity"] in REG["severities"]


def test_distress_signal_not_critical():
    """«руки вверх» бывает и при поправке каски: мастеру только после оператора."""
    sev = {t["code"]: t["severity"] for t in REG["types"]}
    assert sev["distress_signal"] == "high"


def test_type_enums_match_registry():
    assert SCHEMA["properties"]["type"]["enum"] == CODES
    assert COMP["ViolationType"]["enum"] == CODES
    assert COMP["Severity"]["enum"] == REG["severities"]
    assert SCHEMA["properties"]["severity"]["enum"] == REG["severities"]


def test_event_fields_statuses_zone_kind():
    for f in ("track_id", "zone_id", "severity", "incident_id", "count", "first_seen", "last_seen"):
        assert f in SCHEMA["properties"] and f in COMP["EventIn"]["properties"]
        assert f in COMP["Event"]["properties"]
    statuses = ["new", "confirmed", "sent_to_master", "resolved", "false"]
    assert COMP["EventStatus"]["enum"] == statuses
    assert set(COMP["StatusChange"]["required"]) == {"status", "ts", "by"}
    assert "status_history" in COMP["Event"]["required"]
    kinds = set(COMP["Zone"]["properties"]["kind"]["enum"])
    new_kinds = {"under_load", "two_person", "crowd_limit"}
    assert {"machinery", "fence", "height", "other"} | new_kinds <= kinds


def test_get_events_filters():
    params = {p["name"] for p in API["paths"]["/api/events"]["get"]["parameters"]}
    assert {"type", "camera", "zone", "status", "from", "to", "limit", "offset"} <= params


def test_sample_valid_and_covers_new_types():
    events = json.loads((ROOT / "demo" / "events.sample.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER)
    for e in events:
        validator.validate(e)
    assert all(e["type"] in CODES and e["incident_id"] for e in events)


def test_track_id_optional_for_incident_merge():
    # K3: без трека бэкенд склеивает инцидент по камере и типу
    assert "track_id" not in SCHEMA["required"]
    assert "track_id" not in COMP["EventIn"]["required"]
    assert "track_id" not in COMP["Event"]["required"]


def test_auth_paths_and_security():
    paths = API["paths"]
    assert {"401", "429"} <= set(paths["/api/auth/login"]["post"]["responses"])
    assert "/api/auth/logout" in paths
    me = COMP["Me"]
    assert set(me["required"]) == {"login", "name", "role"}
    roles = ["operator", "master", "specialist", "manager", "auditor", "admin"]
    assert COMP["Role"]["enum"] == roles
    schemes = API["components"]["securitySchemes"]
    assert schemes["sessionCookie"]["in"] == "cookie"
    assert schemes["workerToken"]["scheme"] == "bearer"
    assert API["security"] == [{"sessionCookie": []}]
    for path, method in [
        ("/api/events", "post"),
        ("/api/cameras/{id}/frame", "post"),
        ("/api/snapshots", "post"),
        ("/api/metrics", "post"),
    ]:
        assert paths[path][method]["security"] == [{"workerToken": []}], path
    assert {"401", "403"} <= set(paths["/api/events/{id}"]["patch"]["responses"])
    assert {"401", "403"} <= set(paths["/api/cameras/{id}/zones"]["put"]["responses"])
    by = paths["/api/events/{id}"]["patch"]["requestBody"]["content"]["application/json"]
    assert "Игнорируется" in by["schema"]["properties"]["by"]["description"]
    stream = paths["/api/stream"]["get"]["description"]
    assert "event: master" in stream and "event: escalation" in stream


def test_p17_roles_rights_reason():
    paths = API["paths"]
    assert "auditor" in COMP["Role"]["enum"] and "admin" in COMP["Role"]["enum"]
    assert "admin и specialist" in paths["/api/cameras"]["post"]["description"]
    assert {"401", "403"} <= set(paths["/api/cameras"]["post"]["responses"])
    delete = paths["/api/cameras/{id}"]["delete"]
    assert "только admin" in delete["summary"] and "403" in delete["responses"]
    assert "specialist" in paths["/api/cameras/{id}/zones"]["put"]["summary"]
    patch = paths["/api/events/{id}"]["patch"]
    assert "operator и master" in patch["description"] and "403" in patch["responses"]
    assert paths["/api/cameras"]["get"]["security"] == [{"sessionCookie": []}, {"workerToken": []}]
    assert "admin и воркеру" in COMP["Camera"]["properties"]["source"]["description"]
    body = patch["requestBody"]["content"]["application/json"]["schema"]
    assert "reason" in body["properties"] and "reason" not in body["required"]
    reasons = ["not_person", "ppe_present", "wrong_zone", "duplicate", "other"]
    assert COMP["FalseReason"]["enum"] == reasons
    desc = COMP["FalseReason"]["description"]
    assert "false" in desc and "400" in desc
    assert "reason" in COMP["StatusChange"]["properties"]
    assert "reason" not in COMP["StatusChange"]["required"]


def test_at_height():
    t = next(t for t in REG["types"] if t["code"] == "at_height")
    assert t["name"] == "На высоте — проверьте страховку" and t["severity"] == "medium"
    desc = COMP["ViolationType"]["description"]
    assert "height" in desc and "привяз" in desc
    assert "height" in COMP["Zone"]["properties"]["kind"]["enum"]


def test_cheap_patterns():
    """Новые коды совпадают в реестре, схеме, openapi и types.ts; видов зон не добавлено."""
    want = {
        "loitering": ("Долгое нахождение в опасной зоне", "medium"),
        "vehicle_in_zone": ("Техника в запрещённой зоне", "high"),
        "vehicle_collision": ("Встречный курс двух единиц техники", "critical"),
    }
    reg = {t["code"]: t for t in REG["types"]}
    ts = (ROOT / "web/src/domain/types.ts").read_text(encoding="utf-8")
    for code, (name, sev) in want.items():
        assert (reg[code]["name"], reg[code]["severity"]) == (name, sev)
        assert f'{code}: "{name}"' in ts
    assert set(COMP["ViolationType"]["enum"]) == set(CODES)
    assert "danger" in COMP["Zone"]["properties"]["kind"]["enum"]


def test_users_admin_contract():
    P = API["paths"]
    for path, m in [
        ("/api/users", "get"),
        ("/api/users", "post"),
        ("/api/users/{login}", "patch"),
        ("/api/users/{login}/reset-password", "post"),
    ]:
        op = P[path][m]
        assert "403" in op["responses"] and "только admin" in op["description"]
    assert "201" in P["/api/users"]["post"]["responses"]
    assert "409" in P["/api/users/{login}"]["patch"]["responses"]
    assert set(COMP["UserCreate"]["required"]) == {"login", "name", "role"}
    assert set(COMP["UserPatch"]["properties"]) == {"name", "role", "active", "areas"}
    user_fields = {"login", "name", "role", "active", "created_at", "created_by"}
    assert set(COMP["User"]["required"]) == user_fields
    secret = {"password", "password_hash", "hash", "temp_password"}
    assert not secret & set(COMP["User"]["properties"])
    assert "temp_password" in COMP["UserWithTempPassword"]["required"]
    assert "temp_password" in COMP["TempPassword"]["required"]
    pw = P["/api/auth/password"]["post"]
    assert pw["responses"].keys() >= {"204", "400", "401"}
    assert COMP["PasswordChange"]["properties"]["new_password"]["minLength"] == 10
    assert set(COMP["PasswordChange"]["required"]) == {"old_password", "new_password"}
    assert COMP["Me"]["properties"]["must_change_password"]["type"] == "boolean"


def test_areas_contract():
    assert "area" in COMP["Camera"]["properties"]
    assert "area" not in COMP["Camera"]["required"]
    for name in ("User", "Me", "UserCreate", "UserPatch"):
        a = COMP[name]["properties"]["areas"]
        assert a["type"] == "array" and a["items"] == {"type": "string"}
        assert "areas" not in COMP[name].get("required", [])
    d = API["paths"]["/api/stream"]["get"]["description"]
    assert "areas" in d and "нет мастера участка" in d and "обратная совместимость" in d


def test_web_severity_fallback_matches_registry():
    """Запасная критичность в вебе (если воркер не прислал severity) совпадает с реестром."""
    ts = (ROOT / "web/src/domain/events.ts").read_text(encoding="utf-8")
    block = ts.split("const TYPE_SEVERITY", 1)[1].split("};", 1)[0]
    found = dict(re.findall(r'(\w+): "(critical|high|medium|low)"', block))
    assert found == {t["code"]: t["severity"] for t in REG["types"]}


def test_registry_default_on_and_zone_kind_match_rules_yaml():
    """Реестр говорит вебу, что включено по умолчанию и какая зона нужна; правда — в rules.yaml."""
    rules = yaml.safe_load((ROOT / "ml" / "worker" / "rules.yaml").read_text(encoding="utf-8"))
    default, optional = rules["default"], rules["optional"]
    assert not set(default) & set(optional)
    assert set(default) | set(optional) == set(CODES)
    for entry in REG["types"]:
        code = entry["code"]
        params = (default.get(code) if code in default else optional.get(code)) or {}
        assert entry["default_on"] == (code in default), code
        assert entry.get("zone_kind") == params.get("zone_kind"), code


def test_web_rule_catalog_matches_registry():
    ts = (ROOT / "web/src/domain/ruleCatalog.ts").read_text(encoding="utf-8")
    block = ts.split("export const RULE_INFO", 1)[1].split("};", 1)[0]
    web = {}
    for code, body in re.findall(r"^\s+(\w+): \{ (.*?) \},?$", block, re.M):
        web[code] = {
            "default_on": "defaultOn: true" in body,
            "zone_kind": (re.search(r'zoneKind: "(\w+)"', body) or [None, None])[1],
            "severity": re.search(r'severity: "(\w+)"', body)[1],
        }
    assert web == {
        t["code"]: {
            "default_on": t["default_on"],
            "zone_kind": t.get("zone_kind"),
            "severity": t["severity"],
        }
        for t in REG["types"]
    }
