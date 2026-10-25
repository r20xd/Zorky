"""Правила нарушений: геометрия, антидребезг, реестр правил (по файлу на правило)."""

from .base import (
    REGISTRY,
    Detection,
    Rule,
    Verdict,
    Violation,
    ZoneRule,
    ZoneVerdict,
    build_rules,
    discover,
    load_config,
    register,
)
from .debounce import Debouncer
from .geometry import HEAD_PART, BBox, Point, foot_point, in_head_area, point_in_polygon, zone_hit

__all__ = [
    "BBox",
    "Debouncer",
    "Detection",
    "HEAD_PART",
    "Point",
    "REGISTRY",
    "Rule",
    "Verdict",
    "Violation",
    "ZoneRule",
    "ZoneVerdict",
    "build_rules",
    "discover",
    "foot_point",
    "in_head_area",
    "load_config",
    "point_in_polygon",
    "register",
    "zone_hit",
]
