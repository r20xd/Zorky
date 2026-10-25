"""Сценарии работы с событиями: приём от воркера, передача мастеру, смена статуса."""

from __future__ import annotations

import asyncio

from ..domain.models import Event, EventIn, EventStatus
from .hub import Hub
from .notifier import Notifier
from .ports import Store


class EventNotFound(Exception):
    pass


class MasterOffline(Exception):
    """Мастера нет в сети — событие ему не передано."""


class EventService:
    def __init__(self, store: Store, notifier: Notifier, hub: Hub) -> None:
        self._store = store
        self._notifier = notifier
        self._hub = hub

    def ingest(self, data: EventIn) -> Event:
        """Сохраняет событие и рассылает его в живую ленту (новый инцидент или счётчик)."""
        event = self._store.add_event(data)
        # новый инцидент — `violation`; повтор в окне — `update`: «×N» растёт вживую
        self._hub.publish(event, "violation" if event.count == 1 else "update")
        return event

    async def after_ingest(self, event: Event) -> None:
        """Только для нового инцидента: критичный уходит мастеру сразу, остальные — оповещением.

        Повтор в окне лишь обновил инцидент, оповещение по нему уже ушло."""
        if self._notifier.is_critical(event):
            await self.hand_to_master(event)
        else:
            await self._notifier.notify(event)

    async def hand_to_master(self, event: Event, by: str = "system") -> Event | None:
        """Шлёт мастеру; статус sent_to_master — только если мастер в сети."""
        if not await self._notifier.send_to_master(event):
            return None
        done = await asyncio.to_thread(self._store.set_status, event.id, "sent_to_master", by)
        if done is not None and hasattr(self._notifier, "announce"):
            self._notifier.announce(done)
        return done

    async def change_status(
        self, event_id: str, status: EventStatus, by: str, reason: str | None
    ) -> Event:
        if status == "sent_to_master":
            found = self._store.get_event(event_id)
            if found is None:
                raise EventNotFound(event_id)
            handed = await self.hand_to_master(found, by)
            if handed is None:
                raise MasterOffline(event_id)
            return handed
        event = self._store.set_status(event_id, status, by, reason)
        if event is None:
            raise EventNotFound(event_id)
        self._hub.publish(event, "update")  # оператор и мастер видят смену статуса без перезагрузки
        return event
