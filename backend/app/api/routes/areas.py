"""Участки: общий набор правил для камер; создаёт специалист по охране труда."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ...domain.camera_rules import merged_areas
from ...domain.models import Area
from ..container import Container
from ..schemas import AreaIn


def build_router(c: Container) -> APIRouter:
    router = APIRouter()
    guards = c.guards
    Setup = Depends(guards.person("admin", "specialist"))

    def listing() -> list[dict]:
        cameras = c.store.list_cameras()
        return [
            {
                "name": area.name,
                **({"rules": area.rules} if area.rules is not None else {}),
                **({"rule_schedule": area.rule_schedule} if area.rule_schedule else {}),
                "cameras": [cam.id for cam in cameras if cam.area == area.name],
            }
            for area in merged_areas(c.store.list_areas(), cameras)
        ]

    @router.get("/api/areas", dependencies=[Depends(guards.person_or_worker)])
    async def list_areas() -> list[dict]:
        return listing()

    @router.put("/api/areas/{name:path}", dependencies=[Setup])
    async def put_area(name: str, data: AreaIn) -> dict:
        """Создаёт участок или меняет его правила (rules: null — набор по умолчанию)."""
        name = name.strip()
        if not name or len(name) > 64:
            raise HTTPException(400, "название участка: от 1 до 64 символов")
        c.registry_check(data.rules)
        c.store.upsert_area(Area(name=name, rules=data.rules, rule_schedule=data.rule_schedule))
        return next(item for item in listing() if item["name"] == name)

    @router.delete("/api/areas/{name:path}", status_code=204, dependencies=[Setup])
    async def delete_area(name: str) -> Response:
        if any(cam.area == name for cam in c.store.list_cameras()):
            raise HTTPException(409, "на участке есть камеры: сначала переведите их на другой")
        c.store.delete_area(name)
        return Response(status_code=204)

    return router
