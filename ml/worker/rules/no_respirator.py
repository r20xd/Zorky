from __future__ import annotations

from ._head import HeadRule
from .base import register


@register
class NoRespirator(HeadRule):
    """Голова или каска в верхней части человека есть, а респиратора там же нет."""

    name = "no_respirator"

    def violated(self, near: set[str]) -> bool:
        return bool(near & {"head", "helmet"}) and "respirator" not in near
