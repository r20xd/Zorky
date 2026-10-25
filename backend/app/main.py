"""Бэкенд Zorky: события нарушений, живая лента, камеры и зоны — по contracts/openapi.yaml.

Запуск из backend/: python -m uvicorn app.main:app --port 8080 (или py tasks.py demo из корня).
Документация API: http://127.0.0.1:8080/docs

Здесь сборка: настройки из окружения -> хранилища и сервисы -> FastAPI-приложение.
"""

from pathlib import Path

from fastapi import FastAPI

from .api.app import build_app
from .api.container import Container
from .application.auth_service import AuthService
from .application.notifier import Notifier, WebNotifier
from .application.ports import Store
from .infrastructure import config
from .infrastructure.audit import FileAuditLog
from .infrastructure.demo_player import DemoPlayer
from .infrastructure.demo_seed import seed_demo
from .infrastructure.linq_control import LinqControl
from .infrastructure.registry import load_registry
from .infrastructure.sessions import SqliteSessionStore
from .infrastructure.sqlite_store import SqliteStore
from .infrastructure.users_file import JsonUserRepository


def default_notifier() -> Notifier:
    return WebNotifier(escalate_s=config.escalate_s(), registry=load_registry())


def build_auth(
    users_file: Path | None = None, worker_token: str | None = None, db_path: str = ":memory:"
) -> AuthService:
    return AuthService(
        JsonUserRepository(users_file or config.users_path()),
        SqliteSessionStore(db_path),
        FileAuditLog(),
        worker_token,
    )


def create_app(
    store: Store | None = None,
    notifier: Notifier | None = None,
    snapshots_dir: Path | None = None,
    auth: AuthService | None = None,
    linq: LinqControl | None = None,
    demo: DemoPlayer | None = None,
) -> FastAPI:
    db_path = config.db_path() if store is None else ":memory:"
    auth = auth or build_auth(worker_token=config.worker_token(), db_path=db_path)
    if store is None:
        store = SqliteStore(db_path)
        # первый запуск: демо-данные; в демо со входом камеры заводятся только роликами
        if not store.list_cameras() and not config.demo_only():
            seed_demo(store)
    container = Container.build(
        store=store,
        notifier=notifier or default_notifier(),
        auth=auth,
        snapshots_dir=snapshots_dir or config.snapshots_dir(),
        registry=load_registry(),
        linq=linq,
        demo=demo,
    )
    return build_app(container)


app = create_app()
