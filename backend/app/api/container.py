"""Всё, что нужно роутам: сервисы и хранилища, собранные в одном месте."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException

from ..application.auth_service import AuthService
from ..application.event_service import EventService
from ..application.hub import Hub
from ..application.notifier import Notifier
from ..application.ports import Store
from ..domain.models import Metrics
from ..infrastructure.demo_player import DemoPlayer
from ..infrastructure.linq_control import LinqControl
from ..infrastructure.previews import Previews
from ..infrastructure.snapshots import SnapshotStorage
from .guards import Guards


@dataclass
class Container:
    store: Store
    hub: Hub
    notifier: Notifier
    auth: AuthService
    events: EventService
    previews: Previews
    snapshots: SnapshotStorage
    guards: Guards
    registry: dict[str, dict] = field(default_factory=dict)  # код типа -> {name, severity}
    # последние метрики по камере; в памяти, не история
    metrics: dict[str, Metrics] = field(default_factory=dict)
    linq: LinqControl = field(default_factory=LinqControl)
    demo: DemoPlayer = field(default_factory=DemoPlayer)

    def registry_check(self, codes: list[str] | None) -> None:
        """Коды правил должны быть из реестра типов (если реестр загружен)."""
        unknown = [code for code in codes or [] if self.registry and code not in self.registry]
        if unknown:
            raise HTTPException(400, f"неизвестные типы нарушений: {', '.join(unknown)}")

    @classmethod
    def build(
        cls,
        store: Store,
        notifier: Notifier,
        auth: AuthService,
        snapshots_dir: Path,
        registry: dict[str, dict] | None = None,
        linq: LinqControl | None = None,
        demo: DemoPlayer | None = None,
    ) -> Container:
        hub = Hub()
        notifier.bind(store, hub)
        return cls(
            store=store,
            hub=hub,
            notifier=notifier,
            auth=auth,
            events=EventService(store, notifier, hub),
            previews=Previews(),
            snapshots=SnapshotStorage(snapshots_dir),
            guards=Guards(auth),
            registry=registry or {},
            linq=linq or LinqControl(),
            demo=demo or DemoPlayer(),
        )
