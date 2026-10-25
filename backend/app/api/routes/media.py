"""Картинки от воркера: кадры превью (MJPEG) и снимки нарушений."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse

from ...domain.models import Camera
from ...infrastructure import config
from ...infrastructure.previews import BOUNDARY, is_jpeg, mjpeg_stream
from ..container import Container

PHONE_ROLES = ("admin", "operator", "specialist")
PHONE_NAME = "Камера телефона"


def build_router(c: Container) -> APIRouter:
    router = APIRouter()
    guards = c.guards

    async def jpeg_body(request: Request) -> bytes:
        body = await request.body()
        if not is_jpeg(body):
            raise HTTPException(400, "тело должно быть JPEG (image/jpeg)")
        return body

    @router.post(
        "/api/cameras/{camera_id}/frame", status_code=204, dependencies=[Depends(guards.worker)]
    )
    async def put_frame(camera_id: str, request: Request) -> Response:
        c.previews.put_from_worker(camera_id, await jpeg_body(request))
        return Response(status_code=204)

    @router.get("/api/cameras/{camera_id}/mjpeg", dependencies=[Depends(guards.person())])
    async def mjpeg(camera_id: str, request: Request) -> StreamingResponse:
        return StreamingResponse(
            mjpeg_stream(c.previews, camera_id, request.is_disconnected),
            media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    def phone_source(camera_id: str) -> str:
        """Адрес, по которому воркер на этой машине читает кадры телефона (токен — в запросе)."""
        token = config.worker_token()
        query = f"?token={token}" if token else ""
        return f"http://127.0.0.1:{config.port()}/api/phone/{camera_id}.mjpg{query}"

    @router.post(
        "/api/phone/{camera_id}/frame",
        status_code=204,
        dependencies=[Depends(guards.person(*PHONE_ROLES))],
    )
    async def put_phone_frame(camera_id: str, request: Request, name: str = "") -> Response:
        """Кадр с камеры телефона (страница /phone-cam.html): камера заводится сама."""
        body = await jpeg_body(request)
        if not any(cam.id == camera_id for cam in c.store.list_cameras()):
            c.store.upsert_camera(
                Camera(
                    id=camera_id,
                    name=name.strip()[:64] or PHONE_NAME,
                    source=phone_source(camera_id),
                )
            )
        c.previews.put(f"phone:{camera_id}", body)
        if not c.previews.worker_active(camera_id):  # воркера нет: оператор видит сырой кадр
            c.previews.put(camera_id, body)
        return Response(status_code=204)

    @router.get("/api/phone/{camera_id}.mjpg")
    async def phone_stream(camera_id: str, request: Request, token: str = "") -> StreamingResponse:
        """Сырые кадры телефона как MJPEG: воркер (токен в запросе) или вошедший человек."""
        if (
            c.auth.enabled
            and not c.auth.worker_ok(f"Bearer {token}")
            and not guards.is_worker(request)
        ):
            guards.person()(request)  # не воркер: нужен вход человека, иначе 401
        return StreamingResponse(
            mjpeg_stream(c.previews, f"phone:{camera_id}", request.is_disconnected),
            media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/api/snapshots", status_code=201, dependencies=[Depends(guards.worker)])
    async def put_snapshot(request: Request) -> dict:
        body = await jpeg_body(request)
        return {"url": await run_in_threadpool(c.snapshots.save, body)}

    @router.get("/api/snapshots/{name}", dependencies=[Depends(guards.person())])
    def get_snapshot(name: str) -> Response:
        content = c.snapshots.read(name)
        if content is None:
            raise HTTPException(404, "снимок не найден")
        return Response(content=content, media_type="image/jpeg")

    return router
