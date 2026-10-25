import asyncio

from app.application.hub import Hub, event_stream
from app.domain.models import Event

EVENT = Event(
    camera_id="cam-1",
    ts="2026-10-21T10:00:00Z",
    frame_id=1,
    type="no_helmet",
    bbox=[0.1, 0.2, 0.3, 0.9],
    confidence=0.8,
    track_id=3,
    id="evt-000001",
    received_at="2026-10-21T10:00:01Z",
)


async def never_disconnected() -> bool:
    return False


def test_stream_delivers_published_event_and_unsubscribes():
    async def scenario():
        hub = Hub()
        stream = event_stream(hub, never_disconnected, ping_s=5)
        assert (await anext(stream)).startswith(": connected")
        hub.publish(EVENT)
        chunk = await asyncio.wait_for(anext(stream), timeout=2)
        await stream.aclose()
        return chunk, hub.subscribers

    chunk, subscribers = asyncio.run(scenario())
    assert chunk.startswith("event: violation\ndata: ")
    assert '"id":"evt-000001"' in chunk
    assert subscribers == 0


def test_stream_pings_when_idle():
    async def scenario():
        stream = event_stream(Hub(), never_disconnected, ping_s=0.05)
        await anext(stream)
        chunk = await asyncio.wait_for(anext(stream), timeout=2)
        await stream.aclose()
        return chunk

    assert asyncio.run(scenario()) == ": ping\n\n"


def test_slow_subscriber_does_not_block_publish():
    hub = Hub(buffer=2)
    hub.subscribe()
    for _ in range(10):
        hub.publish(EVENT)  # не падает и не ждёт, лишнее отбрасывается
    assert hub.subscribers == 1


def test_stream_writes_event_name_and_escalation_skips_master():
    async def scenario():
        hub = Hub()
        op = event_stream(hub, never_disconnected, ping_s=5)
        ms = event_stream(hub, never_disconnected, ping_s=5, master=True)
        await anext(op)
        await anext(ms)
        hub.publish(EVENT, "master")
        got_op = await asyncio.wait_for(anext(op), timeout=2)
        got_ms = await asyncio.wait_for(anext(ms), timeout=2)
        hub.publish(EVENT, "escalation", operators_only=True)
        esc = await asyncio.wait_for(anext(op), timeout=2)
        try:
            await asyncio.wait_for(anext(ms), timeout=0.2)
            master_got = True
        except asyncio.TimeoutError:
            master_got = False
        return got_op, got_ms, esc, master_got

    got_op, got_ms, esc, master_got = asyncio.run(scenario())
    assert got_op.startswith("event: master\n")
    assert got_ms.startswith("event: master\n")
    assert esc.startswith("event: escalation\n")
    assert not master_got
