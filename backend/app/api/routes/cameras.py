"""Камеры, их правила и зоны на кадре."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from ...domain.camera_rules import effective_rules
from ...domain.models import Area, Camera, Zone
from ..container import Container
from ..schemas import CameraPatchIn


def build_router(c: Container) -> APIRouter:
    router = APIRouter()
    guards = c.guards
    Setup = Depends(guards.person("admin", "specialist"))

    def ensure_area(name: str | None) -> None:
        """Участок, на который встала камера, появляется в списке участков сам."""
        if name and all(a.name != name for a in c.store.list_areas()):
            c.store.upsert_area(Area(name=name))

    def shown(camera: Camera, request: Request) -> Camera:
        """Адрес источника (в нём бывают логин и пароль) видят только admin и воркер."""
        if not c.auth.enabled or guards.is_worker(request):
            return camera
        user = guards.session_user(request)
        if user is not None and user.role == "admin":
            return camera
        return camera.model_copy(update={"source": None})

    def find_camera(camera_id: str) -> Camera:
        camera = next((cam for cam in c.store.list_cameras() if cam.id == camera_id), None)
        if camera is None:
            raise HTTPException(404, "камера не найдена")
        return camera

    @router.get(
        "/api/cameras",
        response_model=list[Camera],
        response_model_exclude_none=True,
        dependencies=[Depends(guards.person_or_worker)],
    )
    async def list_cameras(request: Request) -> list[Camera]:
        return [shown(cam, request) for cam in c.store.list_cameras()]

    @router.post(
        "/api/cameras",
        status_code=201,
        response_model=Camera,
        response_model_exclude_none=True,
        dependencies=[Setup],
    )
    async def upsert_camera(camera: Camera, request: Request) -> Camera:
        user = guards.session_user(request)
        exists = any(cam.id == camera.id for cam in c.store.list_cameras())
        if exists and c.auth.enabled and user is not None and user.role != "admin":
            raise HTTPException(403, "существующую камеру правит администратор")
        c.registry_check(camera.rules)
        ensure_area(camera.area)
        return shown(c.store.upsert_camera(camera), request)

    @router.patch(
        "/api/cameras/{camera_id}",
        response_model=Camera,
        response_model_exclude_none=True,
        dependencies=[Setup],
    )
    async def patch_camera(camera_id: str, patch: CameraPatchIn, request: Request) -> Camera:
        camera = find_camera(camera_id)
        changes = patch.model_dump(exclude_unset=True)
        if "area" in changes:
            changes["area"] = (changes["area"] or "").strip() or None
        c.registry_check(changes.get("rules"))
        ensure_area(changes.get("area"))
        return shown(c.store.upsert_camera(camera.model_copy(update=changes)), request)

    @router.get("/api/cameras/{camera_id}/rules", dependencies=[Depends(guards.person_or_worker)])
    async def get_rules(camera_id: str) -> dict:
        """Что контролировать на камере: enabled — коды правил, null — набор по умолчанию."""
        camera = find_camera(camera_id)
        enabled, source = effective_rules(camera, c.store.list_areas(), datetime.now())
        return {"enabled": enabled, "source": source}

    @router.delete(
        "/api/cameras/{camera_id}", status_code=204, dependencies=[Depends(guards.person("admin"))]
    )
    def delete_camera(camera_id: str) -> Response:
        c.store.delete_camera(camera_id)
        return Response(status_code=204)

    @router.get(
        "/api/cameras/{camera_id}/zones",
        response_model=list[Zone],
        response_model_exclude_none=True,
        dependencies=[Depends(guards.person_or_worker)],
    )
    async def get_zones(camera_id: str) -> list[Zone]:
        return c.store.zones(camera_id)

    @router.put(
        "/api/cameras/{camera_id}/zones",
        response_model=list[Zone],
        response_model_exclude_none=True,
        dependencies=[Depends(guards.person("specialist"))],
    )
    async def put_zones(camera_id: str, zones: list[Zone]) -> list[Zone]:
        return c.store.set_zones(camera_id, zones)

    return router
