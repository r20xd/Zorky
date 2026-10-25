"""Какие нарушения контролирует камера: свои правила, иначе правила участка, иначе по умолчанию."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from .models import Area, Camera, Schedule

RulesSource = Literal["camera", "area", "default"]


def schedule_active(schedule: Schedule, now: datetime) -> bool:
    """Действует ли окно в местное время `now`; start > end - через полночь, равны - всегда."""
    current = f"{now.hour:02d}:{now.minute:02d}"
    if schedule.start == schedule.end:
        return True
    if schedule.start < schedule.end:
        return schedule.start <= current < schedule.end
    return current >= schedule.start or current < schedule.end


def _scheduled(
    rules: list[str], schedule: dict[str, Schedule] | None, now: datetime | None
) -> list[str]:
    """Правила, у которых сейчас действует своё окно (нет окна — действует всегда)."""
    if not schedule or now is None:
        return list(rules)
    return [r for r in rules if r not in schedule or schedule_active(schedule[r], now)]


def effective_rules(
    camera: Camera, areas: list[Area], now: datetime | None = None
) -> tuple[list[str] | None, RulesSource]:
    """(коды правил, откуда они). None — набор по умолчанию у воркера (rules.yaml).

    Если передано `now`, правила вне своего окна расписания в ответ не попадают. Расписание берётся
    на том же уровне (камера или участок), откуда правила."""
    if camera.rules is not None:
        return _scheduled(camera.rules, camera.rule_schedule, now), "camera"
    area = next((a for a in areas if camera.area and a.name == camera.area), None)
    if area is not None and area.rules is not None:
        return _scheduled(area.rules, area.rule_schedule, now), "area"
    return None, "default"


def merged_areas(stored: list[Area], cameras: list[Camera]) -> list[Area]:
    """Участки из базы плюс те, что известны только по камерам (старые данные без записи)."""
    known = {a.name for a in stored}
    extra = sorted({c.area for c in cameras if c.area and c.area not in known})
    return [*stored, *(Area(name=name) for name in extra)]
