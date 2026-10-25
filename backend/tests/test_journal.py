import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.application.notifier import NopNotifier
from app.infrastructure.memory_store import MemoryStore
from app.infrastructure.sqlite_store import SqliteStore
from app.main import create_app


@pytest.fixture(params=[MemoryStore, SqliteStore], ids=["memory", "sqlite"])
def client(request) -> TestClient:
    c = TestClient(create_app(request.param(window_s=0), NopNotifier()))
    rows = [
        ("cam-1", "danger_zone", "z-1", "2026-10-21T10:00:00Z"),
        ("cam-1", "smoking", None, "2026-10-21T11:00:00Z"),
        ("cam-2", "danger_zone", "z-2", "2026-10-21T12:00:00Z"),
        ("cam-2", "no_helmet", "z-2", "2026-10-21T13:00:00Z"),
    ]
    for i, (cam, typ, zone, ts) in enumerate(rows):
        body = {
            "camera_id": cam,
            "ts": ts,
            "frame_id": i,
            "type": typ,
            "bbox": [0.1, 0.1, 0.2, 0.2],
            "confidence": 0.9,
        }
        if zone:
            body["zone_id"] = zone
        assert c.post("/api/events", json=body).status_code == 201
    return c


def frames(response) -> list[int]:
    return [e["frame_id"] for e in response.json()]


@pytest.mark.parametrize(
    "params, expected",
    [
        ({"type": "danger_zone"}, [2, 0]),
        ({"camera": "cam-2"}, [3, 2]),
        ({"zone": "z-2"}, [3, 2]),
        ({"from": "2026-10-21T11:30:00Z"}, [3, 2]),
        ({"to": "2026-10-21T11:00:00Z"}, [1, 0]),
        ({"from": "2026-10-21T10:30:00Z", "to": "2026-10-21T12:30:00Z"}, [2, 1]),
        ({"camera": "cam-2", "type": "danger_zone"}, [2]),
    ],
)
def test_filters(client, params, expected):
    response = client.get("/api/events", params=params)
    assert response.status_code == 200
    assert frames(response) == expected
    assert response.headers["X-Total-Count"] == str(len(expected))


def test_filter_status(client):
    event_id = client.get("/api/events").json()[0]["id"]
    client.patch(f"/api/events/{event_id}", json={"status": "confirmed"})
    response = client.get("/api/events", params={"status": "confirmed"})
    assert frames(response) == [3]
    assert frames(client.get("/api/events", params={"status": "new"})) == [2, 1, 0]


def test_pages(client):
    page = client.get("/api/events", params={"limit": 2, "offset": 1})
    assert frames(page) == [2, 1]
    assert page.headers["X-Total-Count"] == "4"
    assert frames(client.get("/api/events", params={"limit": 2, "offset": 4})) == []


def test_bad_filters(client):
    assert client.get("/api/events", params={"status": "x"}).status_code == 400
    assert client.get("/api/events", params={"from": "вчера"}).status_code == 400
    assert client.get("/api/events", params={"offset": -1}).status_code == 400


def read_csv(response) -> list[list[str]]:
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))


def test_csv_all_and_filters(client):
    event_id = client.get("/api/events", params={"camera": "cam-1", "type": "smoking"}).json()[0][
        "id"
    ]
    client.patch(f"/api/events/{event_id}", json={"status": "false"})
    rows = read_csv(client.get("/api/events.csv"))
    assert rows[0][:7] == ["time", "camera", "zone", "type", "status", "reason", "history"]
    assert rows[0][7:] == [
        "id",
        "area",
        "count",
        "first_seen",
        "last_seen",
        "reaction_by",
        "reaction_at",
        "reaction_s",
        "snapshot",
        "closed_by",
        "closed_at",
    ]
    assert len(rows) == 5
    assert rows[1][1:5] == ["cam-2", "z-2", "no_helmet", "new"]
    rows = read_csv(client.get("/api/events.csv", params={"camera": "cam-1", "status": "false"}))
    assert len(rows) == 2
    assert rows[1][1:5] == ["cam-1", "", "smoking", "false"]
    assert "false" in rows[1][6]
    rows = read_csv(
        client.get("/api/events.csv", params={"zone": "z-2", "from": "2026-10-21T12:30:00Z"})
    )
    assert [r[3] for r in rows[1:]] == ["no_helmet"]
    assert len(read_csv(client.get("/api/events.csv", params={"type": "danger_zone"}))) == 3


def test_csv_is_evidence_who_reacted_and_when(client):
    event = client.get("/api/events", params={"camera": "cam-1", "type": "smoking"}).json()[0]
    before = read_csv(client.get("/api/events.csv", params={"camera": "cam-1", "type": "smoking"}))
    head = before[0]
    assert before[1][head.index("reaction_by")] == ""  # реакции ещё не было
    client.patch(f"/api/events/{event['id']}", json={"status": "confirmed", "by": "ivan"})
    row = read_csv(client.get("/api/events.csv", params={"camera": "cam-1", "type": "smoking"}))[1]
    assert row[head.index("id")] == event["id"]
    assert row[head.index("reaction_by")] == "ivan"
    assert row[head.index("reaction_at")] and row[head.index("reaction_s")].isdigit()
    assert "confirmed ivan" in row[head.index("history")]
    assert row[head.index("closed_by")] == ""  # подтверждено, но ещё не закрыто
    client.patch(f"/api/events/{event['id']}", json={"status": "resolved", "by": "petr"})
    row = read_csv(client.get("/api/events.csv", params={"camera": "cam-1", "type": "smoking"}))[1]
    assert row[head.index("closed_by")] == "petr" and row[head.index("closed_at")]
    assert (
        row[head.index("reaction_by")] == "ivan"
    )  # первая реакция осталась за тем, кто был первым
