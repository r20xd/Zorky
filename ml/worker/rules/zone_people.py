"""Правила уровня зоны: одиночная работа (lone_worker) и скопление (overcrowding)."""

from __future__ import annotations

from ._temporal import positive
from .base import ZoneRule, ZoneVerdict, register
from .geometry import people_in_zone


class _Counting(ZoneRule):
    """Условие на число людей в зоне должно держаться duration_s подряд."""

    zone_kind = ""
    duration_s = 0.0

    def __init__(self, zone_kind: str, duration_s: float) -> None:
        self.zone_kind = zone_kind
        self.duration_s = positive(duration_s, "duration_s")
        self.now = 0.0
        self.since: dict[str, float] = {}

    def on_frame(self, now: float) -> None:
        if now < self.now:  # время пошло назад (новое видео) — история недействительна
            self.since.clear()
        self.now = now

    def bad(self, count: int) -> bool:
        raise NotImplementedError

    def check_frame(self, people, zones):
        out = []
        seen = set()
        for zone in zones:
            if zone.get("kind") != self.zone_kind:
                continue
            zid = str(zone["id"])
            seen.add(zid)
            if self.bad(len(people_in_zone(people, zone))):
                start = self.since.setdefault(zid, self.now)
                out.append(ZoneVerdict(zone, self.now - start >= self.duration_s))
            else:
                self.since.pop(zid, None)
                out.append(ZoneVerdict(zone, False))
        for zid in set(self.since) - seen:  # зону убрали
            del self.since[zid]
        return out


@register
class LoneWorker(_Counting):
    name = "lone_worker"

    def __init__(self, zone_kind: str = "two_person", duration_s: float = 30.0) -> None:
        """В зоне ровно один человек дольше duration_s."""
        super().__init__(zone_kind, duration_s)

    def bad(self, count):
        return count == 1


@register
class Overcrowding(_Counting):
    name = "overcrowding"

    def __init__(
        self, zone_kind: str = "crowd_limit", max_people: int = 5, duration_s: float = 10.0
    ) -> None:
        """В зоне больше max_people человек дольше duration_s."""
        super().__init__(zone_kind, duration_s)
        self.max_people = int(max_people)

    def bad(self, count):
        return count > self.max_people
