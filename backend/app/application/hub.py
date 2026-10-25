"""Живая лента: рассылка новых событий подписчикам Server-Sent Events."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

from ..domain.models import Event


class Hub:
    """Медленный подписчик пропускает события, а не тормозит остальных."""

    def __init__(self, buffer: int = 64) -> None:
        self._buffer = buffer
        # очередь -> (сессия master?, участки мастера). В очереди пары (имя SSE-события, событие)
        self._subscribers: dict[asyncio.Queue[tuple[str, Event]], tuple[bool, tuple[str, ...]]] = {}

    def subscribe(
        self, master: bool = False, areas: tuple[str, ...] | list[str] = ()
    ) -> asyncio.Queue[tuple[str, Event]]:
        queue: asyncio.Queue[tuple[str, Event]] = asyncio.Queue(maxsize=self._buffer)
        self._subscribers[queue] = (master, tuple(areas))
        return queue

    def unsubscribe(self, queue: asyncio.Queue[tuple[str, Event]]) -> None:
        self._subscribers.pop(queue, None)

    def publish(self, event: Event, name: str = "violation", operators_only: bool = False) -> None:
        for queue, (master, _) in list(self._subscribers.items()):
            if operators_only and master:
                continue
            try:
                queue.put_nowait((name, event))
            except asyncio.QueueFull:
                pass  # подписчик отстаёт — пропускаем событие для него

    def publish_master(self, event: Event, area: str | None) -> bool:
        """Событие `master`: операторам всем; мастерам - участка (без areas - всем).

        Нет ни одного подходящего мастера - уходит всем мастерам в сети, ответ False."""

        def fits(areas: tuple[str, ...]) -> bool:
            return not area or not areas or area in areas

        subs = list(self._subscribers.items())
        matched = any(m and fits(a) for _, (m, a) in subs)
        for queue, (master, areas) in subs:
            if master and matched and not fits(areas):
                continue
            try:
                queue.put_nowait(("master", event))
            except asyncio.QueueFull:
                pass
        return matched

    @property
    def subscribers(self) -> int:
        return len(self._subscribers)

    @property
    def masters(self) -> int:
        """Сколько сессий master сейчас на ленте."""
        return sum(m for m, _ in self._subscribers.values())


async def event_stream(
    hub: Hub,
    is_disconnected: Callable[[], Awaitable[bool]],
    ping_s: float = 15.0,
    master: bool = False,
    areas: tuple[str, ...] | list[str] = (),
) -> AsyncIterator[str]:
    """Поток SSE: `event: violation` (а также `master`, `escalation`) и `: ping`, если тихо."""
    queue = hub.subscribe(master, areas)
    try:
        yield ": connected\n\n"
        while not await is_disconnected():
            try:
                name, event = await asyncio.wait_for(queue.get(), timeout=ping_s)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            yield f"event: {name}\ndata: {event.model_dump_json(exclude_none=True)}\n\n"
    finally:
        hub.unsubscribe(queue)
