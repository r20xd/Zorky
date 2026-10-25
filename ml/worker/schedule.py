"""Расписание зон: зона с `schedule` действует только в свои часы (местное время площадки)."""

from __future__ import annotations

from datetime import time


def _clock(text: str) -> time:
    hours, minutes = text.split(":")
    return time(int(hours), int(minutes))


def zone_active(zone: dict, now: time) -> bool:
    """Нет расписания — всегда. start > end — окно через полночь. start == end — всегда."""
    schedule = zone.get("schedule")
    if not schedule:
        return True
    try:
        start, end = _clock(schedule["start"]), _clock(schedule["end"])
    except (KeyError, ValueError):
        return True  # битое расписание не должно выключать контроль
    if start == end:
        return True
    return start <= now < end if start < end else now >= start or now < end


def active_zones(zones: list[dict], now: time) -> list[dict]:
    return [zone for zone in zones if zone_active(zone, now)]
