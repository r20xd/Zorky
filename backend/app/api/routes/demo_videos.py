"""Демо: свои видео вместо камер по кругу (только при MOSPROM_DEMO=1, роль admin)."""

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from ...domain.models import Camera
from ...infrastructure import config
from ...infrastructure.demo_player import MAX_UPLOAD_BYTES, DemoPlayerError
from ..container import Container
from ..schemas import DemoPlayIn, DemoStopIn


def build_router(c: Container) -> APIRouter:
    router = APIRouter()
    admin = Depends(c.guards.person("admin"))

    def need_demo() -> None:
        if not config.demo_mode():
            raise HTTPException(404, "доступно только в демо-режиме")

    def cameras_on(path: Path) -> list[Camera]:
        """Камеры, чей источник — этот файл (режим с распознаванием: файл читает воркер)."""
        return [cam for cam in c.store.list_cameras() if cam.source and Path(cam.source) == path]

    def players() -> list[dict]:
        found = c.demo.players()
        taken = {p["camera_id"] for p in found}
        for video in c.demo.videos():
            try:
                path = c.demo.path_of(video["name"])
            except DemoPlayerError:
                continue
            for cam in cameras_on(path):
                if cam.id not in taken:
                    taken.add(cam.id)
                    found.append({"camera_id": cam.id, "video": video["name"]})
        return found

    def state() -> dict:
        return {
            "enabled": config.demo_mode(),
            "videos": c.demo.videos() if config.demo_mode() else [],
            "players": players() if config.demo_mode() else [],
            "folder": str(c.demo.folder),
        }

    @router.get("/api/demo/videos", dependencies=[admin])
    def list_videos() -> dict:
        return state()

    @router.post("/api/demo/videos", status_code=201, dependencies=[admin])
    async def upload_video(request: Request, name: str) -> dict:
        """Тело запроса — файл как есть (без multipart); имя — в ?name=."""
        need_demo()
        try:
            target = c.demo.target_for_upload(name)
        except DemoPlayerError as exc:
            raise HTTPException(400, str(exc)) from exc
        part = target.with_name(target.name + ".part")
        size = 0
        try:
            with part.open("wb") as out:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "файл больше 2 ГБ")
                    out.write(chunk)
            if size == 0:
                raise HTTPException(400, "пустой файл")
            os.replace(part, target)
        finally:
            part.unlink(missing_ok=True)
        return state()

    @router.post("/api/demo/play", status_code=202, dependencies=[admin])
    def play(data: DemoPlayIn) -> dict:
        need_demo()
        try:
            if data.detect:  # кадры читает воркер (с рамками и нарушениями), а не зацикливатель
                path = c.demo.prepare_detect(data.camera_id, data.video)
                if cameras_on(path):  # ролик уже идёт как камера: вторую не заводим
                    return state()
            else:
                path = c.demo.play(data.camera_id, data.video, f"http://127.0.0.1:{config.port()}")
        except DemoPlayerError as exc:
            raise HTTPException(400, str(exc)) from exc
        existing = next((cam for cam in c.store.list_cameras() if cam.id == data.camera_id), None)
        if existing is not None:
            # без распознавания кадры шлёт зацикливатель: файл-источник воркеру не нужен
            source = str(path) if data.detect else None
            c.store.upsert_camera(existing.model_copy(update={"source": source}))
        if existing is None:
            title = (data.name or "").strip()[:64] or (path.stem[:64] or data.camera_id)
            # источник - файл только у камер с распознаванием: воркер не должен дублировать кадры
            source = str(path) if data.detect else None
            c.store.upsert_camera(Camera(id=data.camera_id, name=title, source=source))
        return state()

    @router.post("/api/demo/stop", dependencies=[admin])
    def stop(data: DemoStopIn) -> dict:
        need_demo()
        c.demo.stop(data.camera_id)
        # камера с распознаванием: снимаем источник, а камеру и журнал нарушений сохраняем
        for cam in c.store.list_cameras():
            if cam.id != data.camera_id or not cam.source:
                continue
            for video in c.demo.videos():
                try:
                    path = c.demo.path_of(video["name"])
                except DemoPlayerError:
                    continue
                if Path(cam.source) == path:
                    c.store.upsert_camera(cam.model_copy(update={"source": None}))
                    break
        return state()

    @router.delete("/api/demo/videos/{name}", status_code=204, dependencies=[admin])
    def delete_video(name: str) -> Response:
        need_demo()
        try:
            path = c.demo.path_of(name)
        except DemoPlayerError as exc:
            raise HTTPException(404, str(exc)) from exc
        if c.demo.folder not in path.parents:
            raise HTTPException(403, "удалять можно только загруженные видео")
        busy = [p["camera_id"] for p in players() if p["video"] == path.name]
        if busy:
            raise HTTPException(409, f"ролик занят камерой «{busy[0]}»: сначала остановите её")
        path.unlink(missing_ok=True)
        return Response(status_code=204)

    return router
