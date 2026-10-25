"""Правила инцидентов: что считать повтором, как растёт счётчик, фильтры журнала.

Чистые функции без хранилища и без ввода-вывода: одни и те же правила у памяти и у SQLite.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .models import Event, EventIn, EventStatus, StatusChange

IOU_MIN = 0.3
CENTER_MAX = 0.15


def new_incident(data: EventIn, event_id: str) -> Event:
    fields = data.model_dump() | {
        "incident_id": event_id,
        "count": 1,
        "first_seen": data.ts,
        "last_seen": data.ts,
    }
    return Event(**fields, id=event_id, received_at=datetime.now(timezone.utc))


def with_status(event: Event, status: EventStatus, by: str, reason: str | None = None) -> Event:
    change = StatusChange(status=status, ts=datetime.now(timezone.utc), by=by, reason=reason)
    history = [*event.status_history, change]
    return event.model_copy(update={"status": status, "status_history": history})


def repeat(event: Event, data: EventIn, window_s: float) -> Event | None:
    """Повтор в окне от last_seen: счётчик растёт, кадр и рамка обновляются. Иначе None."""
    last = event.last_seen or event.ts
    if abs((data.ts - last).total_seconds()) > window_s:
        return None
    return event.model_copy(
        update={
            "count": event.count + 1,
            "last_seen": max(last, data.ts),
            "frame_id": data.frame_id,
            "bbox": data.bbox,
            "confidence": data.confidence,
            "snapshot_url": data.snapshot_url or event.snapshot_url,
        }
    )


def reaction(event: Event) -> StatusChange | None:
    """Первая реакция человека: смена статуса не системой (автопередача мастеру — не реакция)."""
    return next((h for h in event.status_history if h.status != "new" and h.by != "system"), None)


def closure(event: Event) -> StatusChange | None:
    """Кто и когда закрыл инцидент: последняя смена на resolved или false."""
    return next(
        (h for h in reversed(event.status_history) if h.status in ("resolved", "false")), None
    )


def _iou(a: list[float], b: list[float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _near(a: list[float], b: list[float]) -> bool:
    """Рамки близки: IoU >= 0.3 или центры ближе 0.15 кадра."""
    if _iou(a, b) >= IOU_MIN:
        return True
    dx = (a[0] + a[2] - b[0] - b[2]) / 2
    dy = (a[1] + a[3] - b[1] - b[3]) / 2
    return (dx * dx + dy * dy) ** 0.5 < CENTER_MAX


def same_incident(old: Event, data: EventIn, window_s: float) -> bool:
    """Тот же инцидент: камера, тип, зона, окно; затем трек, событие зоны или близкие рамки."""
    if (old.camera_id, old.type, old.zone_id) != (data.camera_id, data.type, data.zone_id):
        return False
    if abs((data.ts - (old.last_seen or old.ts)).total_seconds()) > window_s:
        return False
    if data.track_id is not None and data.track_id == old.track_id:
        return True
    if data.track_id is None and data.zone_id is not None:
        return True  # событие уровня зоны: ключ (камера, тип, зона)
    return _near(old.bbox, data.bbox)


def matches(
    event: Event,
    camera_id: str | None,
    type: str | None,
    zone_id: str | None,
    status: str | None,
    ts_from: datetime | None,
    ts_to: datetime | None,
) -> bool:
    return (
        (not camera_id or event.camera_id == camera_id)
        and (not type or event.type == type)
        and (not zone_id or event.zone_id == zone_id)
        and (not status or event.status == status)
        and (ts_from is None or event.ts >= ts_from)
        and (ts_to is None or event.ts <= ts_to)
    )


def paginate(found: list[Event], limit: int | None, offset: int) -> list[Event]:
    """Страница из отфильтрованных (новые сверху)."""
    end = None if limit is None else offset + limit
    return found[offset:end]
