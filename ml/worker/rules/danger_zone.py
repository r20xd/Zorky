from __future__ import annotations

from .base import Detection, Rule, Verdict, register
from .geometry import foot_point, zone_hit


@register
class DangerZone(Rule):
    """Ноги человека в зоне вида zone_kind (по умолчанию danger)."""

    name = "danger_zone"

    def __init__(self, zone_kind: str = "danger") -> None:
        self.zone_kind = zone_kind

    def check(self, person: Detection, detections: list[Detection], zones: list[dict]) -> Verdict:
        zone = zone_hit(foot_point(person.bbox), zones, self.zone_kind)
        return Verdict(zone is not None, zone["id"] if zone else None)
