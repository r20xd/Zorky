"""Хранилище в памяти: для тестов, при перезапуске бэкенда журнал пропадает."""

from __future__ import annotations

from datetime import datetime

from ..domain.incidents import matches, new_incident, paginate, repeat, same_incident, with_status
from ..domain.models import Area, Camera, Event, EventIn, EventStatus, Zone
from . import config


class MemoryStore:
    def __init__(self, window_s: float | None = None) -> None:
        self._window_s = config.incident_window_s() if window_s is None else window_s
        self._events: list[Event] = []
        self._cameras: dict[str, Camera] = {}
        self._zones: dict[str, list[Zone]] = {}
        self._areas: dict[str, Area] = {}

    def add_event(self, data: EventIn) -> Event:
        for i in range(len(self._events) - 1, -1, -1):
            old = self._events[i]
            if same_incident(old, data, self._window_s):
                merged = repeat(old, data, self._window_s)
                if merged:
                    self._events[i] = merged
                    return merged
        event = new_incident(data, f"evt-{len(self._events) + 1:06d}")
        self._events.append(event)
        return event

    def get_event(self, event_id: str) -> Event | None:
        return next((e for e in self._events if e.id == event_id), None)

    def list_events(
        self,
        limit: int | None = 100,
        camera_id: str | None = None,
        type: str | None = None,
        zone_id: str | None = None,
        status: str | None = None,
        ts_from: datetime | None = None,
        ts_to: datetime | None = None,
        offset: int = 0,
    ) -> list[Event]:
        """Новые сверху; limit=None — без ограничения."""
        found = self._filtered(camera_id, type, zone_id, status, ts_from, ts_to)
        return paginate(found, limit, offset)

    def _filtered(self, *args) -> list[Event]:
        return [e for e in reversed(self._events) if matches(e, *args)]

    def count_events(
        self,
        camera_id: str | None = None,
        type: str | None = None,
        zone_id: str | None = None,
        status: str | None = None,
        ts_from: datetime | None = None,
        ts_to: datetime | None = None,
    ) -> int:
        return len(self._filtered(camera_id, type, zone_id, status, ts_from, ts_to))

    def set_status(
        self, event_id: str, status: EventStatus, by: str = "operator", reason: str | None = None
    ) -> Event | None:
        for i, event in enumerate(self._events):
            if event.id == event_id:
                self._events[i] = with_status(event, status, by, reason)
                return self._events[i]
        return None

    def list_cameras(self) -> list[Camera]:
        return list(self._cameras.values())

    def upsert_camera(self, camera: Camera) -> Camera:
        self._cameras[camera.id] = camera
        return camera

    def delete_camera(self, camera_id: str) -> None:
        self._cameras.pop(camera_id, None)
        self._zones.pop(camera_id, None)

    def list_areas(self) -> list[Area]:
        return list(self._areas.values())

    def upsert_area(self, area: Area) -> Area:
        self._areas[area.name] = area
        return area

    def delete_area(self, name: str) -> None:
        self._areas.pop(name, None)

    def zones(self, camera_id: str) -> list[Zone]:
        return list(self._zones.get(camera_id, []))

    def set_zones(self, camera_id: str, zones: list[Zone]) -> list[Zone]:
        self._zones[camera_id] = list(zones)
        return self.zones(camera_id)
