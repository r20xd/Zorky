import asyncio

from fastapi.testclient import TestClient

from app.application.hub import Hub
from app.application.notifier import NopNotifier, WebNotifier, critical_types
from app.domain.models import Camera, Event, EventIn
from app.infrastructure.memory_store import MemoryStore
from app.infrastructure.registry import load_registry
from app.main import create_app, default_notifier
from tests.test_api import VALID


def make(master: bool = True, **kwargs):
    store = MemoryStore()
    notifier = WebNotifier(registry=load_registry(), **kwargs)
    app = create_app(store, notifier)
    hub = None
    # Hub создаётся внутри create_app; достаём через привязку уведомителя
    hub = notifier._hub
    queue = hub.subscribe(master=master)
    return TestClient(app), store, notifier, hub, queue


def test_registry_critical_includes_vehicle_proximity():
    crit = critical_types(load_registry())
    assert {"fall", "immobile", "vehicle_proximity"} <= crit
    assert "no_helmet" not in crit
    assert load_registry()["vehicle_proximity"]["name"]


def test_default_notifier_is_web():
    assert isinstance(default_notifier(), WebNotifier)


def test_master_online_gets_sent_to_master_and_sse_event():
    client, store, _, _, queue = make()
    event = store.add_event(EventIn(**VALID))
    r = client.patch(f"/api/events/{event.id}", json={"status": "sent_to_master"})
    assert r.status_code == 200 and r.json()["status"] == "sent_to_master"
    name, sent = queue.get_nowait()
    assert name == "master" and sent.id == event.id and sent.status == "sent_to_master"


def test_no_master_gives_409_and_status_stays():
    client, store, _, _, _ = make(master=False)  # на ленте только оператор
    event = store.add_event(EventIn(**VALID))
    r = client.patch(f"/api/events/{event.id}", json={"status": "sent_to_master"})
    assert r.status_code == 409 and r.json() == {"error": "мастер не в сети"}
    assert store.list_events()[0].status == "new"
    assert client.patch("/api/events/nope", json={"status": "sent_to_master"}).status_code in (
        404,
        409,
    )


def test_vehicle_proximity_goes_to_master_automatically():
    client, store, _, _, queue = make()
    body = {**VALID, "type": "vehicle_proximity", "track_id": 5}
    assert client.post("/api/events", json=body).status_code == 201
    assert store.list_events()[0].status == "sent_to_master"
    names = [queue.get_nowait()[0] for _ in range(2)]
    assert names == ["violation", "master"]


def test_noncritical_stays_new():
    client, store, _, _, _ = make()
    client.post("/api/events", json=VALID)
    assert store.list_events()[0].status == "new"


def test_critical_without_master_stays_new():
    client, store, _, _, _ = make(master=False)
    client.post("/api/events", json={**VALID, "type": "fall", "track_id": 1})
    assert store.list_events()[0].status == "new"


def test_escalation_after_timeout_with_fake_sleep():
    async def scenario(answer: bool):
        waited = []

        async def fake_sleep(seconds):
            waited.append(seconds)

        store, hub = MemoryStore(), Hub()
        notifier = WebNotifier(escalate_s=60, sleep=fake_sleep)
        notifier.bind(store, hub)
        operator, master = hub.subscribe(), hub.subscribe(master=True)
        event = store.add_event(EventIn(**VALID))
        done = store.set_status(event.id, "sent_to_master", "master")
        if answer:
            done = store.set_status(event.id, "resolved", "master")
        notifier.announce(done)
        await asyncio.gather(*notifier._tasks)
        return waited, operator, master

    waited, operator, master = asyncio.run(scenario(answer=False))
    assert waited == [60]
    assert [operator.get_nowait()[0], operator.get_nowait()[0]] == ["master", "escalation"]
    assert master.get_nowait()[0] == "master" and master.empty()

    _, operator, _ = asyncio.run(scenario(answer=True))
    assert operator.get_nowait()[0] == "master" and operator.empty()


def test_title_from_registry():
    notifier = WebNotifier(registry=load_registry())
    event = Event(**{**VALID, "id": "e", "received_at": VALID["ts"], "type": "vehicle_proximity"})
    assert notifier.title(event) != "vehicle_proximity"


def test_nop_notifier_never_hands_over():
    store = MemoryStore()
    event = store.add_event(EventIn(**VALID))
    r = TestClient(create_app(store, NopNotifier())).patch(
        f"/api/events/{event.id}", json={"status": "sent_to_master"}
    )
    assert r.status_code == 409


def _areas_scenario(masters: dict[str, tuple[str, ...]], camera_area: str | None):
    async def scenario():
        async def fake_sleep(seconds):
            return None

        store, hub = MemoryStore(), Hub()
        notifier = WebNotifier(escalate_s=60, sleep=fake_sleep)
        notifier.bind(store, hub)
        store.upsert_camera(Camera(id=VALID["camera_id"], name="K", area=camera_area))
        queues = {k: hub.subscribe(master=True, areas=a) for k, a in masters.items()}
        operator = hub.subscribe()
        event = store.add_event(EventIn(**VALID))
        done = store.set_status(event.id, "sent_to_master", "master")
        notifier.announce(done)
        await asyncio.gather(*notifier._tasks)
        return queues, operator

    return asyncio.run(scenario())


def test_master_of_other_area_does_not_see_incident():
    queues, operator = _areas_scenario({"a": ("A",), "b": ("B",), "all": ()}, "A")
    assert queues["a"].get_nowait()[0] == "master"
    assert queues["all"].get_nowait()[0] == "master"
    assert queues["b"].empty()
    assert [operator.get_nowait()[0], operator.get_nowait()[0]] == ["master", "escalation"]


def test_master_without_areas_sees_everything():
    queues, _ = _areas_scenario({"all": ()}, "B")
    assert queues["all"].get_nowait()[0] == "master"


def test_no_master_of_area_goes_to_all_masters_and_escalates_with_note():
    queues, operator = _areas_scenario({"b": ("B",), "c": ("C",)}, "A")
    assert queues["b"].get_nowait()[0] == "master" and queues["c"].get_nowait()[0] == "master"
    assert operator.get_nowait()[0] == "master"
    name, esc = operator.get_nowait()
    assert name == "escalation" and esc.note == "нет мастера участка"
    assert operator.empty()


def test_camera_and_user_accept_area():
    assert Camera(id="c", name="n", area="A").area == "A"
    assert Camera(id="c", name="n").area is None


def test_status_change_and_repeat_are_published_as_update():
    client, store, notifier, hub, queue = make()
    first = client.post("/api/events", json=VALID).json()
    assert queue.get_nowait()[0] == "violation"
    again = client.post("/api/events", json={**VALID, "frame_id": 2}).json()
    assert again["id"] == first["id"] and again["count"] == 2
    name, got = queue.get_nowait()
    assert name == "update" and got.count == 2  # повтор: «×2» растёт вживую
    changed = client.patch(f"/api/events/{first['id']}", json={"status": "confirmed"})
    assert changed.status_code == 200
    name, got = queue.get_nowait()
    assert name == "update" and got.status == "confirmed"
