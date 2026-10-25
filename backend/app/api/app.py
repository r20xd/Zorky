"""Сборка FastAPI-приложения из роутов."""

from fastapi import Depends, FastAPI

from .container import Container
from .errors import install_error_handlers
from .routes import (
    areas,
    auth,
    cameras,
    demo_videos,
    events,
    health,
    linq,
    media,
    metrics,
    users,
)


def build_app(container: Container) -> FastAPI:
    app = FastAPI(
        title="Zorky backend",
        version="0.1.0",
        dependencies=[Depends(container.guards.origin_guard)],
    )
    install_error_handlers(app)
    for module in (
        auth,
        users,
        health,
        events,
        areas,
        cameras,
        media,
        metrics,
        linq,
        demo_videos,
    ):
        app.include_router(module.build_router(container))
    return app
