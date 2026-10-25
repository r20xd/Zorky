"""Подключение к LinQ из дашборда: статус, подключить/отключить, выбор устройства."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from ...infrastructure.linq_control import LinqError
from ..container import Container
from ..schemas import LinqDeviceIn


def build_router(c: Container) -> APIRouter:
    router = APIRouter()
    guards = c.guards
    read = Depends(guards.person("admin", "manager"))
    write = Depends(guards.person("admin"))

    @router.get("/api/linq", dependencies=[read])
    def get_linq() -> dict:
        return c.linq.status()

    @router.post("/api/linq/connect", status_code=202, dependencies=[write])
    def connect() -> JSONResponse:
        try:
            c.linq.connect()
        except LinqError as exc:
            raise HTTPException(409, str(exc)) from exc
        return JSONResponse(status_code=202, content=c.linq.status())

    @router.post("/api/linq/disconnect", status_code=202, dependencies=[write])
    def disconnect() -> JSONResponse:
        try:
            c.linq.disconnect()
        except LinqError as exc:
            raise HTTPException(409, str(exc)) from exc
        return JSONResponse(status_code=202, content=c.linq.status())

    @router.put("/api/linq/config", dependencies=[write])
    def put_config(data: LinqDeviceIn) -> dict:
        try:
            c.linq.set_device(data.device)
        except LinqError as exc:
            raise HTTPException(400, str(exc)) from exc
        return c.linq.status()

    return router
