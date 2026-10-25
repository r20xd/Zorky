"""Метрики инференса от воркера: последние значения по камере."""

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from ...domain.models import Metrics
from ..container import Container


def build_router(c: Container) -> APIRouter:
    router = APIRouter()
    guards = c.guards

    @router.post("/api/metrics", status_code=204, dependencies=[Depends(guards.worker)])
    async def put_metrics(data: Metrics) -> Response:
        c.metrics[data.camera_id] = data
        return Response(status_code=204)

    @router.get(
        "/api/metrics",
        response_model=list[Metrics],
        response_model_exclude_none=True,
        dependencies=[Depends(guards.person())],
    )
    async def get_metrics() -> list[Metrics]:
        return sorted(c.metrics.values(), key=lambda m: m.camera_id)

    return router
