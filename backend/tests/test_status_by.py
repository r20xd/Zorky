from app.domain.models import Event, EventIn
from app.infrastructure.memory_store import MemoryStore


def test_status_history_has_by() -> None:
    store = MemoryStore()
    ev = store.add_event(
        EventIn(
            camera_id="c",
            ts="2026-01-01T00:00:00Z",
            frame_id=1,
            type="no_helmet",
            bbox=[0.1, 0.1, 0.2, 0.2],
            confidence=0.9,
        )
    )
    out = store.set_status(ev.id, "confirmed", "telegram:ivan")
    assert out is not None and out.status_history[-1].by == "telegram:ivan"
    assert store.set_status(ev.id, "resolved").status_history[-1].by == "operator"
    assert isinstance(out, Event)
