from fastapi import APIRouter

from ..container import Container


def build_router(c: Container) -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def health() -> dict:
        return {"status": "ok", "stream_subscribers": c.hub.subscribers}

    return router
