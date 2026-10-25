from fastapi.testclient import TestClient

from app.application.notifier import NopNotifier
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


def test_data_survives_restart(tmp_path):
    path = tmp_path / "db" / "m.db"
    first = TestClient(create_app(SqliteStore(path), NopNotifier()))
    event = first.post("/api/events", json=VALID).json()
    first.patch(f"/api/events/{event['id']}", json={"status": "confirmed"})
    first.post("/api/cameras", json={"id": "cam-7", "name": "Цех 7"})
    zones = [{"id": "z-9", "kind": "danger", "polygon": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5]]}]
    first.put("/api/cameras/cam-7/zones", json=zones)

    second = TestClient(create_app(SqliteStore(path), NopNotifier()))
    stored = second.get("/api/events").json()[0]
    assert stored["status"] == "confirmed"
    assert [h["status"] for h in stored["status_history"]] == ["confirmed"]
    skip = ("status", "status_history")
    assert {k: v for k, v in stored.items() if k not in skip} == {
        k: v for k, v in event.items() if k not in skip
    }
    assert second.get("/api/cameras").json() == [{"id": "cam-7", "name": "Цех 7"}]
    assert second.get("/api/cameras/cam-7/zones").json() == zones
    assert (
        second.post(
            "/api/events", json={**VALID, "track_id": 8, "bbox": [0.1, 0.1, 0.2, 0.2]}
        ).json()["id"]
        != event["id"]
    )


def test_default_store_is_sqlite_and_seeds_once(tmp_path, monkeypatch):
    monkeypatch.setenv("MOSPROM_DB", str(tmp_path / "d.db"))
    first = TestClient(create_app(notifier=NopNotifier()))
    first.put("/api/cameras/cam-1/zones", json=[])
    second = TestClient(create_app(notifier=NopNotifier()))
    assert len(second.get("/api/cameras").json()) == 2
    assert second.get("/api/cameras/cam-1/zones").json() == []


def _at(seconds, **extra):
    return {**VALID, "ts": f"2026-10-21T10:00:{seconds:02d}Z", **extra}


def test_repeats_make_one_incident_and_one_alert():
    class Spy(NopNotifier):
        def __init__(self):
            self.sent = []

        async def notify(self, event):
            self.sent.append(event.id)

    spy = Spy()
    client = TestClient(create_app(SqliteStore(window_s=10), spy))
    for i in range(5):
        r = client.post("/api/events", json=_at(i, frame_id=i, snapshot_url=f"/s/{i}.jpg"))
        assert r.status_code == 201
    events = client.get("/api/events").json()
    assert len(events) == 1 and len(spy.sent) == 1
    assert events[0]["count"] == 5
    assert events[0]["incident_id"] == events[0]["id"]
    assert events[0]["first_seen"].startswith("2026-10-21T10:00:00")
    assert events[0]["last_seen"].startswith("2026-10-21T10:00:04")
    assert events[0]["snapshot_url"] == "/s/4.jpg" and events[0]["frame_id"] == 4

    client.post("/api/events", json=_at(40))  # окно 10 с прошло: новый инцидент
    assert len(client.get("/api/events").json()) == 2
    assert len(spy.sent) == 2


def test_no_track_merges_by_camera_and_type():
    from app.infrastructure.memory_store import MemoryStore

    for store in (SqliteStore(window_s=10), MemoryStore(window_s=10)):
        client = TestClient(create_app(store, NopNotifier()))
        base = {k: v for k, v in VALID.items() if k != "track_id"}
        for i in range(3):
            body = {**base, "ts": f"2026-10-21T10:00:0{i}Z"}
            assert client.post("/api/events", json=body).status_code == 201
        client.post("/api/events", json={**base, "type": "smoking"})
        events = client.get("/api/events").json()
        assert sorted(e["count"] for e in events) == [1, 3]
        assert "track_id" not in events[-1] or events[-1]["track_id"] is None


def test_other_track_or_camera_is_other_incident():
    client = TestClient(create_app(SqliteStore(window_s=10), NopNotifier()))
    client.post("/api/events", json=_at(0))
    client.post("/api/events", json=_at(1, track_id=8, bbox=[0.7, 0.7, 0.9, 0.9]))
    client.post("/api/events", json=_at(2, camera_id="cam-2"))
    assert [e["count"] for e in client.get("/api/events").json()] == [1, 1, 1]


def _spy_client(store):
    sent = []

    class Spy(NopNotifier):
        async def notify(self, event):
            sent.append(event.id)

    return TestClient(create_app(store, Spy())), sent


def test_new_track_same_place_is_same_incident():
    from app.infrastructure.memory_store import MemoryStore

    for store in (SqliteStore(window_s=10), MemoryStore(window_s=10)):
        client, sent = _spy_client(store)
        client.post("/api/events", json=_at(0, track_id=1, bbox=[0.40, 0.4, 0.5, 0.6]))
        client.post("/api/events", json=_at(3, track_id=9, bbox=[0.42, 0.4, 0.52, 0.6]))
        events = client.get("/api/events").json()
        assert len(events) == 1 and events[0]["count"] == 2 and len(sent) == 1
        # далеко в кадре: второй человек
        client.post("/api/events", json=_at(4, track_id=10, bbox=[0.8, 0.1, 0.9, 0.3]))
        assert len(client.get("/api/events").json()) == 2 and len(sent) == 2
        # через окно: новый инцидент
        client.post("/api/events", json=_at(30, track_id=9, bbox=[0.42, 0.4, 0.52, 0.6]))
        assert len(client.get("/api/events").json()) == 3


def test_zone_level_events_merge_by_zone():
    from app.infrastructure.memory_store import MemoryStore

    for store in (SqliteStore(window_s=10), MemoryStore(window_s=10)):
        client, _ = _spy_client(store)
        base = {k: v for k, v in VALID.items() if k != "track_id"}
        client.post(
            "/api/events", json={**base, **_at(0), "zone_id": "z1", "bbox": [0, 0, 0.2, 0.2]}
        )
        client.post(
            "/api/events", json={**base, **_at(1), "zone_id": "z1", "bbox": [0.7, 0.7, 1, 1]}
        )
        client.post(
            "/api/events", json={**base, **_at(2), "zone_id": "z2", "bbox": [0, 0, 0.2, 0.2]}
        )
        assert sorted(e["count"] for e in client.get("/api/events").json()) == [1, 2]


def test_get_event_finds_old_events_beyond_any_list_limit():
    from app.domain.models import EventIn
    from app.infrastructure.memory_store import MemoryStore

    for store in (MemoryStore(), SqliteStore()):
        first = store.add_event(EventIn(**VALID))
        for i in range(1100):  # дальше окна склейки и дальше лимита списка в 1000
            store.add_event(
                EventIn(**{**VALID, "type": "no_helmet", "track_id": 1000 + i, "frame_id": i})
            )
        assert store.get_event(first.id).id == first.id
        assert store.get_event("evt-nope") is None


def test_periodic_confirmations_keep_one_incident_for_a_long_violation():
    """Воркер подтверждает длящееся нарушение каждые 15 с: окно 30 с продлевается, инцидент один."""
    client = TestClient(create_app(SqliteStore(window_s=30), NopNotifier()))
    for i in range(8):  # две минуты
        ts = f"2026-10-21T10:{i * 15 // 60:02d}:{i * 15 % 60:02d}Z"
        assert (
            client.post("/api/events", json={**VALID, "ts": ts, "frame_id": i}).status_code == 201
        )
    events = client.get("/api/events").json()
    assert len(events) == 1 and events[0]["count"] == 8
    assert events[0]["last_seen"].startswith("2026-10-21T10:01:45")
