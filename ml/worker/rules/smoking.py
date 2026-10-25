from __future__ import annotations

from ._head import HeadRule
from .base import register


@register
class Smoking(HeadRule):
    """Сигарета в верхней части рамки человека."""

    name = "smoking"

    def violated(self, near: set[str]) -> bool:
        return "cigarette" in near
