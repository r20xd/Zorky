from __future__ import annotations

from ._head import HeadRule
from .base import register


@register
class NoHelmet(HeadRule):
    """Голова в верхней части человека есть, а каски там же нет."""

    name = "no_helmet"

    def violated(self, near: set[str]) -> bool:
        return "head" in near and "helmet" not in near
