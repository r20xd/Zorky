"""Порты: что прикладному слою нужно от внешнего мира. Реализации — в infrastructure."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..domain.models import Area, Camera, Event, EventIn, EventStatus, Zone


class Store(Protocol):
    """Журнал событий, камеры и зоны. Любая реализация должна пройти те же тесты."""

    def add_event(self, data: EventIn) -> Event:
        """Новый инцидент (count == 1) или повтор в окне: тот же id, count + 1."""
        ...

    def get_event(self, event_id: str) -> Event | None: ...
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
    ) -> list[Event]: ...
    def count_events(
        self,
        camera_id: str | None = None,
        type: str | None = None,
        zone_id: str | None = None,
        status: str | None = None,
        ts_from: datetime | None = None,
        ts_to: datetime | None = None,
    ) -> int: ...
    def set_status(
        self, event_id: str, status: EventStatus, by: str = "operator", reason: str | None = None
    ) -> Event | None: ...
    def list_cameras(self) -> list[Camera]: ...
    def upsert_camera(self, camera: Camera) -> Camera: ...
    def delete_camera(self, camera_id: str) -> None: ...
    def zones(self, camera_id: str) -> list[Zone]: ...
    def set_zones(self, camera_id: str, zones: list[Zone]) -> list[Zone]: ...
    def list_areas(self) -> list[Area]: ...
    def upsert_area(self, area: Area) -> Area: ...
    def delete_area(self, name: str) -> None: ...


class UserRepository(Protocol):
    """Пользователи: {login: запись}. Запись — как в файле (соль и хеш внутри)."""

    @property
    def location(self) -> str: ...

    def current(self) -> dict[str, dict]:
        """Актуальный набор; если источник изменился снаружи — перечитывается."""
        ...

    def save(self, users: dict[str, dict]) -> None: ...


class SessionStore(Protocol):
    """Сессии входа: в хранилище только хеш токена."""

    def add(self, token_hash: str, login: str, expires: float) -> None: ...
    def find(self, token_hash: str) -> tuple[str, float] | None: ...
    def delete(self, token_hash: str) -> None: ...
    def delete_for_login(self, login: str) -> None: ...


class AuditLog(Protocol):
    def record(self, actor: str, action: str, target: str, **details: object) -> None: ...
