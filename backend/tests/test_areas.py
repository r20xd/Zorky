"""Участки, правила по камерам и расписание зон: что настраивает специалист по охране труда."""

import pytest
from fastapi.testclient import TestClient

from app import cli
from app.application.notifier import NopNotifier
from app.domain.camera_rules import effective_rules, merged_areas
from app.domain.models import Area, Camera
from app.infrastructure.memory_store import MemoryStore
from app.infrastructure.sqlite_store import SqliteStore
from app.main import build_auth, create_app


def test_effective_rules_priority():
    areas = [Area(name="Цех 1", rules=["fall"]), Area(name="Склад")]
    own = Camera(id="a", name="a", area="Цех 1", rules=["smoking"])
    assert effective_rules(own, areas) == (["smoking"], "camera")
    assert effective_rules(Camera(id="b", name="b", area="Цех 1"), areas) == (["fall"], "area")
    assert effective_rules(Camera(id="c", name="c", area="Склад"), areas) == (None, "default")
    assert effective_rules(Camera(id="d", name="d"), areas) == (None, "default")
    empty = Camera(id="e", name="e", area="Цех 1", rules=[])  # «ничего не контролировать» — не None
    assert effective_rules(empty, areas) == ([], "camera")


def test_merged_areas_adds_areas_known_only_by_cameras():
    cams = [Camera(id="a", name="a", area="Старый"), Camera(id="b", name="b", area="Цех 1")]
    names = [a.name for a in merged_areas([Area(name="Цех 1")], cams)]
    assert names == ["Цех 1", "Старый"]


@pytest.fixture(params=[MemoryStore, SqliteStore], ids=["memory", "sqlite"])
def stores(request):
    return request.param()


def test_store_areas_roundtrip(stores):
    stores.upsert_area(Area(name="Цех 1", rules=["fall"]))
    stores.upsert_area(Area(name="Цех 1", rules=["fall", "smoking"]))
    stores.upsert_area(Area(name="Склад"))
    assert [(a.name, a.rules) for a in stores.list_areas()] == [
        ("Цех 1", ["fall", "smoking"]),
        ("Склад", None),
    ]
    stores.delete_area("Цех 1")
    assert [a.name for a in stores.list_areas()] == ["Склад"]
    stores.upsert_camera(Camera(id="c", name="c", area="Склад", rules=["fall"]))
    assert stores.list_cameras()[0].rules == ["fall"]


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("MOSPROM_AUDIT", str(tmp_path / "audit.log"))
    users = tmp_path / "users.json"
    for login, role in (("spec", "specialist"), ("adm", "admin"), ("op", "operator")):
        cli.add_user(users, login, role, login, f"pw-{login}-long-1")
    app = create_app(MemoryStore(), NopNotifier(), auth=build_auth(users_file=users))
    return app


def client(app, login):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"login": login, "password": f"pw-{login}-long-1"})
    assert r.status_code == 200
    return c


def test_specialist_creates_area_and_sets_rules(api):
    spec = client(api, "spec")
    assert spec.put("/api/areas/Новый цех", json={"rules": ["fall", "smoking"]}).status_code == 200
    areas = spec.get("/api/areas").json()
    assert areas == [{"name": "Новый цех", "rules": ["fall", "smoking"], "cameras": []}]
    assert spec.put("/api/areas/%20", json={}).status_code == 400
    assert spec.put("/api/areas/Цех", json={"rules": ["Bad Code"]}).status_code == 400
    unknown = spec.put("/api/areas/Цех", json={"rules": ["no_such_type"]})
    assert unknown.status_code == 400 and "no_such_type" in unknown.json()["error"]
    assert client(api, "op").put("/api/areas/Х", json={}).status_code == 403


def test_new_area_flow_camera_inherits_area_rules(api):
    spec = client(api, "spec")
    spec.put("/api/areas/Склад", json={"rules": ["danger_zone"]})
    cam = {"id": "cam-9", "name": "Вебкамера", "source": "0", "area": "Склад"}
    assert spec.post("/api/cameras", json=cam).status_code == 201
    assert spec.get("/api/areas").json()[0]["cameras"] == ["cam-9"]
    assert spec.get("/api/cameras/cam-9/rules").json() == {
        "enabled": ["danger_zone"],
        "source": "area",
    }
    r = spec.patch("/api/cameras/cam-9", json={"rules": ["fall"]})
    assert r.status_code == 200 and r.json()["rules"] == ["fall"]
    assert spec.get("/api/cameras/cam-9/rules").json()["source"] == "camera"
    r = spec.patch("/api/cameras/cam-9", json={"rules": None})  # вернуть правила участка
    assert r.json().get("rules") is None
    assert spec.get("/api/cameras/cam-9/rules").json()["source"] == "area"
    assert spec.get("/api/cameras").json()[0].get("source") is None  # адрес специалисту не виден
    assert "source" not in r.json()  # и в ответе на правку тоже
    created = spec.post(
        "/api/cameras", json={"id": "cam-8", "name": "x", "source": "rtsp://u:p@h/1"}
    )
    assert created.status_code == 201 and "source" not in created.json()
    assert client(api, "adm").get("/api/cameras").json()[0]["source"] == "0"


def test_patch_camera_moves_between_areas_and_keeps_source(api):
    adm = client(api, "adm")
    adm.post("/api/cameras", json={"id": "c1", "name": "c1", "source": "rtsp://x/1"})
    spec = client(api, "spec")
    assert spec.patch("/api/cameras/c1", json={"area": "Цех 2"}).json()["area"] == "Цех 2"
    assert [a["name"] for a in spec.get("/api/areas").json()] == ["Цех 2"]  # участок создался сам
    assert adm.get("/api/cameras").json()[0]["source"] == "rtsp://x/1"
    assert spec.patch("/api/cameras/c1", json={"area": ""}).json().get("area") is None
    assert spec.patch("/api/cameras/nope", json={"name": "x"}).status_code == 404


def test_specialist_cannot_overwrite_existing_camera(api):
    adm = client(api, "adm")
    adm.post("/api/cameras", json={"id": "c1", "name": "c1", "source": "rtsp://x/1"})
    spec = client(api, "spec")
    assert spec.post("/api/cameras", json={"id": "c1", "name": "c1"}).status_code == 403


def test_delete_area_only_when_empty(api):
    spec = client(api, "spec")
    spec.put("/api/areas/Цех", json={})
    spec.post("/api/cameras", json={"id": "c1", "name": "c1", "source": "0", "area": "Цех"})
    assert spec.delete("/api/areas/Цех").status_code == 409
    spec.patch("/api/cameras/c1", json={"area": None})
    assert spec.delete("/api/areas/Цех").status_code == 204


def test_zone_schedule_roundtrip_and_validation(api):
    spec = client(api, "spec")
    zone = {
        "id": "z-1",
        "kind": "danger",
        "polygon": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5]],
        "schedule": {"start": "07:55", "end": "12:00"},
    }
    assert spec.put("/api/cameras/cam-1/zones", json=[zone]).json()[0]["schedule"] == {
        "start": "07:55",
        "end": "12:00",
    }
    bad = {**zone, "schedule": {"start": "25:00", "end": "12:00"}}
    assert spec.put("/api/cameras/cam-1/zones", json=[bad]).status_code == 400


def test_area_name_with_slash_and_special_characters(api):
    from urllib.parse import quote

    spec = client(api, "spec")
    for name in ("Цех 1/2", "Цех?1", "100%", "Склад #3"):
        assert spec.put(f"/api/areas/{quote(name, safe='')}", json={}).status_code == 200, name
    assert {"Цех 1/2", "Цех?1", "100%", "Склад #3"} <= {
        a["name"] for a in spec.get("/api/areas").json()
    }
    assert spec.delete(f"/api/areas/{quote('Цех 1/2', safe='')}").status_code == 204


def test_camera_name_and_area_are_validated(api):
    spec = client(api, "spec")
    spec.post("/api/cameras", json={"id": "c1", "name": "Камера", "source": "0"})
    assert spec.patch("/api/cameras/c1", json={"name": None}).status_code == 400
    assert spec.patch("/api/cameras/c1", json={"name": "   "}).status_code == 400
    assert spec.patch("/api/cameras/c1", json={"name": "Новое имя"}).json()["name"] == "Новое имя"
    too_long = "x" * 65
    assert (
        spec.post("/api/cameras", json={"id": "c2", "name": "n", "area": too_long}).status_code
        == 400
    )
    assert spec.patch("/api/cameras/c1", json={"area": too_long}).status_code == 400
    assert spec.post("/api/cameras", json={"id": "c3", "name": "  "}).status_code == 400


def test_csv_cells_are_not_formulas(api):
    from app.domain.models import EventIn

    store = MemoryStore()
    store.add_event(
        EventIn(
            camera_id="=HYPERLINK()",
            ts="2026-10-21T10:00:00Z",
            frame_id=1,
            type="fall",
            bbox=[0.1, 0.1, 0.2, 0.2],
            confidence=0.9,
            zone_id="@zone",
        )
    )
    c = TestClient(create_app(store, NopNotifier()))
    text = c.get("/api/events.csv").content.decode("utf-8-sig")
    assert "'=HYPERLINK()" in text and "'@zone" in text


def test_rule_schedule_filters_by_time():
    from datetime import datetime

    from app.domain.models import Schedule

    at = lambda h, m: datetime(2026, 10, 24, h, m)  # noqa: E731
    area = Area(
        name="Цех 1",
        rules=["no_helmet", "smoking", "fall"],
        rule_schedule={
            "smoking": Schedule(start="08:00", end="17:00"),
            "fall": Schedule(start="22:00", end="06:00"),  # через полночь
        },
    )
    cam = Camera(id="a", name="a", area="Цех 1")
    assert effective_rules(cam, [area], at(12, 0)) == (["no_helmet", "smoking"], "area")
    assert effective_rules(cam, [area], at(23, 30)) == (["no_helmet", "fall"], "area")
    assert effective_rules(cam, [area], at(3, 0)) == (["no_helmet", "fall"], "area")
    assert effective_rules(cam, [area], at(17, 0)) == (["no_helmet"], "area")
    # без времени расписание не применяется (как раньше)
    assert effective_rules(cam, [area]) == (["no_helmet", "smoking", "fall"], "area")
    # расписание камеры — там, где правила камеры
    own = Camera(
        id="b",
        name="b",
        rules=["smoking"],
        rule_schedule={"smoking": Schedule(start="08:00", end="09:00")},
    )
    assert effective_rules(own, [], at(12, 0)) == ([], "camera")
