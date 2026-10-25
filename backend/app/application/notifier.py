"""Канал мастеру внутри контура: событие `master` в SSE мастеру в сети, эскалация операторам."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from ..domain.models import Event
from .hub import Hub
from .ports import Store

log = logging.getLogger("notify")

DEFAULT_ESCALATE_S = 60.0
# Запасной набор, если реестра нарушений нет рядом (образ Docker без contracts/)
FALLBACK_CRITICAL = frozenset({"fall", "immobile", "vehicle_proximity"})


def critical_types(registry: dict[str, dict] | None = None) -> frozenset[str]:
    """Критичные коды из реестра нарушений; пустой реестр — запасной набор."""
    if not registry:
        return FALLBACK_CRITICAL
    return frozenset(c for c, t in registry.items() if t.get("severity") == "critical")


class Notifier(Protocol):
    async def notify(self, event: Event) -> None: ...

    async def send_to_master(self, event: Event) -> bool: ...

    def bind(self, store: Store, hub: Hub) -> None: ...

    def is_critical(self, event: Event) -> bool: ...

    def title(self, event: Event) -> str: ...


class EscalationEvent(Event):
    """Эскалация с пометкой: уходит в SSE как Event плюс поле note."""

    note: str


class WebNotifier:
    """Мастеру — только через ленту /api/stream: нет сессии master в сети — не передано."""

    def __init__(
        self,
        escalate_s: float | None = None,
        registry: dict[str, dict] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._escalate_s = DEFAULT_ESCALATE_S if escalate_s is None else escalate_s
        self._registry = registry or {}
        self._critical = critical_types(self._registry)
        self._sleep = sleep
        self._store: Store | None = None
        self._hub: Hub | None = None
        self._tasks: set[asyncio.Task] = set()

    def bind(self, store: Store, hub: Hub) -> None:
        self._store = store
        self._hub = hub

    def is_critical(self, event: Event) -> bool:
        return event.type in self._critical

    def title(self, event: Event) -> str:
        """Название типа из реестра; нет в реестре — сам код."""
        return self._registry.get(event.type, {}).get("name", event.type)

    async def notify(self, event: Event) -> None:
        return None  # операторы видят событие в ленте; отдельного канала нет

    async def send_to_master(self, event: Event) -> bool:
        if self._hub is None or self._hub.masters == 0:
            log.warning("мастеру не передано (%s): мастер не в сети", event.id)
            return False
        return True

    def announce(self, event: Event) -> None:
        """Событие `master` в ленту и таймер эскалации; вызывать после смены статуса."""
        if self._hub is None:
            return
        area = next((c.area for c in self._store.list_cameras() if c.id == event.camera_id), None)
        if not self._hub.publish_master(event, area):
            log.warning("мастера участка %s нет в сети (%s)", area, event.id)
            noted = EscalationEvent.model_validate(
                {**event.model_dump(), "note": "нет мастера участка"}
            )
            self._hub.publish(noted, "escalation", operators_only=True)
            return
        try:
            task = asyncio.get_running_loop().create_task(self._escalate(event))
        except RuntimeError:
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _escalate(self, event: Event) -> None:
        await self._sleep(self._escalate_s)
        if self._store is None or self._hub is None:
            return
        found = await asyncio.to_thread(self._store.get_event, event.id)
        if found is not None and found.status == "sent_to_master":
            log.warning("мастер не ответил за %s с (%s)", self._escalate_s, event.id)
            self._hub.publish(found, "escalation", operators_only=True)


class NopNotifier:
    """Для тестов без ленты: мастеру ничего не уходит."""

    async def notify(self, event: Event) -> None:
        return None

    async def send_to_master(self, event: Event) -> bool:
        return False

    def bind(self, store: Store, hub: Hub) -> None:
        return None

    def is_critical(self, event: Event) -> bool:
        return False

    def title(self, event: Event) -> str:
        return event.type
