import pytest
from fastapi.testclient import TestClient

from app.application.notifier import NopNotifier
from app.infrastructure.memory_store import MemoryStore
from app.infrastructure.sqlite_store import SqliteStore
from app.main import create_app

VALID = {
    "camera_id": "cam-1",
    "ts": "2026-10-21T10:00:00Z",
    "frame_id": 1,
    "type": "danger_zone",
    "bbox": [0.6, 0.3, 0.7, 0.9],
    "confidence": 0.9,
    "track_id": 7,
}


@pytest.fixture(params=[MemoryStore, SqliteStore], ids=["memory", "sqlite"])
def client(request) -> TestClient:
    """Все тесты API идут на обоих хранилищах."""
    return TestClient(create_app(request.param(), NopNotifier()))


def test_create_and_list_event(client):
    response = client.post("/api/events", json=VALID)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["id"] and created["status"] == "new"
    assert "snapshot_url" not in created  # пустые поля не отдаём, как в контракте
    listed = client.get("/api/events", params={"camera_id": "cam-1"}).json()
    assert [event["id"] for event in listed] == [created["id"]]


@pytest.mark.parametrize(
    "change",
    [
        {"extra": 1},
        {"bbox": [0.6, 0.3, 0.1, 0.6]},  # [x, y, w, h] вместо [x1, y1, x2, y2]
        {"camera_id": ""},
        {"ts": "2026-10-21T10:00:00"},  # без часового пояса
        {"confidence": 1.2},
    ],
)
def test_rejects_event_outside_contract(client, change):
    response = client.post("/api/events", json={**VALID, **change})
    assert response.status_code == 400
    assert "error" in response.json()


def test_rejects_event_without_required_field(client):
    body = {key: value for key, value in VALID.items() if key != "confidence"}
    assert client.post("/api/events", json=body).status_code == 400


def test_operator_marks_false_positive(client):
    created = client.post("/api/events", json=VALID).json()
    response = client.patch(f"/api/events/{created['id']}", json={"status": "false"})
    assert response.status_code == 200
    assert response.json()["status"] == "false"
    missing = client.patch("/api/events/evt-missing", json={"status": "confirmed"})
    assert missing.status_code == 404
    assert missing.json() == {"error": "событие не найдено"}


def test_zones_round_trip_and_validation(client):
    zones = [{"id": "z-9", "kind": "danger", "polygon": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5]]}]
    assert client.put("/api/cameras/cam-9/zones", json=zones).status_code == 200
    assert client.get("/api/cameras/cam-9/zones").json() == zones
    two_points = [{"id": "z-9", "kind": "danger", "polygon": [[0.1, 0.1], [0.5, 0.1]]}]
    assert client.put("/api/cameras/cam-9/zones", json=two_points).status_code == 400


METRICS = {
    "camera_id": "cam-1",
    "device": "linq",
    "fps": 24.5,
    "inference_ms": 12.0,
    "e2e_ms": 80.0,
    "ts": "2026-10-21T10:00:00Z",
}


def test_metrics_latest_per_camera(client):
    assert client.get("/api/metrics").json() == []
    assert client.post("/api/metrics", json=METRICS).status_code == 204
    newer = {**METRICS, "fps": 30.0}
    assert client.post("/api/metrics", json=newer).status_code == 204
    other = {k: v for k, v in METRICS.items() if k != "e2e_ms"} | {"camera_id": "cam-2"}
    assert client.post("/api/metrics", json=other).status_code == 204
    listed = client.get("/api/metrics").json()
    assert [m["camera_id"] for m in listed] == ["cam-1", "cam-2"]
    assert listed[0]["fps"] == 30.0 and listed[0]["e2e_ms"] == 80.0
    assert "e2e_ms" not in listed[1]


@pytest.mark.parametrize(
    "change", [{"device": "tpu"}, {"fps": -1}, {"extra": 1}, {"ts": "2026-10-21T10:00:00"}]
)
def test_metrics_rejects_outside_contract(client, change):
    assert client.post("/api/metrics", json={**METRICS, **change}).status_code == 400


def test_demo_seed_has_zone_for_fake_worker():
    client = TestClient(create_app(notifier=NopNotifier()))
    cameras = [camera["id"] for camera in client.get("/api/cameras").json()]
    assert cameras == ["cam-1", "cam-2"]
    zone = client.get("/api/cameras/cam-1/zones").json()[0]
    assert zone["id"] == "z-1" and zone["kind"] == "danger"


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200 and response.json()["status"] == "ok"


def test_events_newest_first_with_filters_and_limit(client):
    for frame, kind in enumerate(["no_helmet", "smoking", "no_helmet"]):
        x = 0.1 + 0.4 * frame  # люди в разных местах кадра: разные инциденты
        client.post(
            "/api/events",
            json={
                **VALID,
                "frame_id": frame,
                "type": kind,
                "track_id": frame,
                "bbox": [x, 0.1, x + 0.1, 0.3],
            },
        )
    client.post("/api/events", json={**VALID, "camera_id": "cam-2"})
    frames = [
        e["frame_id"] for e in client.get("/api/events", params={"camera_id": "cam-1"}).json()
    ]
    assert frames == [2, 1, 0]
    by_type = client.get("/api/events", params={"type": "no_helmet"}).json()
    assert [e["frame_id"] for e in by_type] == [2, 0]
    assert len(client.get("/api/events", params={"limit": 2}).json()) == 2
    assert client.get("/api/events", params={"camera_id": "nope"}).json() == []


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 1001}])
def test_events_list_rejects_bad_query(client, params):
    response = client.get("/api/events", params=params)
    assert response.status_code == 400 and "error" in response.json()


@pytest.mark.parametrize("body", [{"status": "done"}, {}, {"status": "new", "x": 1}])
def test_patch_rejects_bad_body(client, body):
    created = client.post("/api/events", json=VALID).json()
    response = client.patch(f"/api/events/{created['id']}", json=body)
    assert response.status_code == 400 and "error" in response.json()


def test_cameras_upsert_and_list(client):
    assert client.get("/api/cameras").json() == []
    camera = {"id": "cam-5", "name": "Цех 5"}
    response = client.post("/api/cameras", json=camera)
    assert response.status_code == 201 and response.json() == camera
    client.post("/api/cameras", json={**camera, "name": "Цех 5Б", "source": "rtsp://x"})
    assert client.get("/api/cameras").json() == [{**camera, "name": "Цех 5Б", "source": "rtsp://x"}]


@pytest.mark.parametrize(
    "body", [{"id": "cam-5"}, {"id": "", "name": "x"}, {"id": "a", "name": "b", "z": 1}]
)
def test_camera_rejects_bad_body(client, body):
    response = client.post("/api/cameras", json=body)
    assert response.status_code == 400 and "error" in response.json()


def test_zones_empty_by_default_and_bad_kind(client):
    assert client.get("/api/cameras/none/zones").json() == []
    bad = [{"id": "z", "kind": "bogus", "polygon": [[0, 0], [1, 0], [1, 1]]}]
    assert client.put("/api/cameras/cam-9/zones", json=bad).status_code == 400
    outside = [{"id": "z", "kind": "danger", "polygon": [[0, 0], [2, 0], [1, 1]]}]
    assert client.put("/api/cameras/cam-9/zones", json=outside).status_code == 400


def test_snapshots_round_trip(tmp_path):
    client = TestClient(create_app(MemoryStore(), NopNotifier(), snapshots_dir=tmp_path / "s"))
    jpeg = b"\xff\xd8\xff\xe0snapshot"
    created = client.post("/api/snapshots", content=jpeg, headers={"content-type": "image/jpeg"})
    assert created.status_code == 201
    url = created.json()["url"]
    assert url.startswith("/api/snapshots/")
    got = client.get(url)
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/jpeg"
    assert got.content == jpeg
    assert len(list((tmp_path / "s").iterdir())) == 1


def test_snapshots_errors(tmp_path):
    client = TestClient(create_app(MemoryStore(), NopNotifier(), snapshots_dir=tmp_path))
    assert client.post("/api/snapshots", content=b"not a jpeg").status_code == 400
    missing = client.get("/api/snapshots/snap-000000000000.jpg")
    assert missing.status_code == 404
    assert "error" in missing.json()
    assert client.get("/api/snapshots/..%2Fsecret.jpg").status_code == 404


def test_legacy_status_names_are_read_as_new(client):
    created = client.post("/api/events", json=VALID).json()
    url = f"/api/events/{created['id']}"
    assert client.patch(url, json={"status": "acknowledged"}).json()["status"] == "confirmed"
    assert client.patch(url, json={"status": "false_positive"}).json()["status"] == "false"
    # мастера нет в сети — 409, статус прежний
    assert client.patch(url, json={"status": "sent_to_master"}).status_code == 409
    assert client.get("/api/events").json()[0]["status"] == "false"
    assert client.patch(url, json={"status": "bogus"}).status_code == 400


def test_new_fields_optional_and_any_type_code(client):
    body = {**VALID, "type": "no_gloves", "severity": "high", "count": 3}
    created = client.post("/api/events", json=body).json()
    assert created["type"] == "no_gloves"
    assert created["severity"] == "high"
    assert created["count"] == 1  # счётчик ведёт бэкенд
    assert created["status_history"] == []
