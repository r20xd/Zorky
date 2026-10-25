"""Демо-камеры и зоны для первого запуска на пустой базе."""

from __future__ import annotations

from ..application.ports import Store
from ..domain.models import Area, Camera, Zone

DEMO_AREAS = [Area(name="Цех 1"), Area(name="Склад")]
DEMO_CAMERAS = [
    Camera(id="cam-1", name="Цех 1 — пресс", area="Цех 1"),
    Camera(id="cam-2", name="Склад ГСМ", area="Склад"),
]
# z-1 совпадает с зоной синтетики воркера (--fake) в ml/tests/test_pipeline.py
DEMO_ZONES = {
    "cam-1": [
        Zone(
            id="z-1",
            kind="danger",
            name="Зона пресса",
            polygon=[(0.6, 0.3), (0.95, 0.3), (0.95, 0.95), (0.6, 0.95)],
        )
    ],
    "cam-2": [
        Zone(
            id="z-2",
            kind="flammable",
            name="Горючие материалы",
            polygon=[(0.0, 0.2), (0.3, 0.2), (0.3, 1.0), (0.0, 1.0)],
        )
    ],
}


def seed_demo(store: Store) -> None:
    for area in DEMO_AREAS:
        store.upsert_area(area)
    for camera in DEMO_CAMERAS:
        store.upsert_camera(camera)
    for camera_id, zones in DEMO_ZONES.items():
        store.set_zones(camera_id, zones)
