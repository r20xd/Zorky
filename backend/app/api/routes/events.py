"""Журнал событий: список, CSV, приём от воркера, смена статуса, живая лента (SSE)."""

import csv
import io
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import AwareDatetime

from ...application.event_service import EventNotFound, MasterOffline
from ...application.hub import event_stream
from ...domain.incidents import closure, reaction
from ...domain.models import Event, EventIn, EventStatus, StatusUpdate
from ...domain.users import User
from ..container import Container

# первые семь колонок прежние; дальше то, что нужно как доказательство: кто и когда отреагировал
CSV_COLUMNS = [
    "time",
    "camera",
    "zone",
    "type",
    "status",
    "reason",
    "history",
    "id",
    "area",
    "count",
    "first_seen",
    "last_seen",
    "reaction_by",
    "reaction_at",
    "reaction_s",
    "snapshot",
    "closed_by",
    "closed_at",
]


def _filters(
    camera: str | None,
    camera_id: str | None,
    type: str | None,
    zone: str | None,
    status: str | None,
    ts_from: AwareDatetime | None,
    ts_to: AwareDatetime | None,
) -> dict:
    return {
        "camera_id": camera or camera_id,
        "type": type,
        "zone_id": zone,
        "status": status,
        "ts_from": ts_from,
        "ts_to": ts_to,
    }


def build_router(c: Container) -> APIRouter:
    router = APIRouter()
    guards = c.guards
    Anyone = Annotated[User, Depends(guards.person())]
    Marker = Annotated[User, Depends(guards.person("operator", "master"))]

    @router.get(
        "/api/events",
        response_model=list[Event],
        response_model_exclude_none=True,
        dependencies=[Depends(guards.person())],
    )
    def list_events(
        response: Response,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
        camera: str | None = None,
        camera_id: str | None = None,
        type: str | None = None,
        zone: str | None = None,
        status: EventStatus | None = None,
        ts_from: Annotated[AwareDatetime | None, Query(alias="from")] = None,
        ts_to: Annotated[AwareDatetime | None, Query(alias="to")] = None,
    ) -> list[Event]:
        flt = _filters(camera, camera_id, type, zone, status, ts_from, ts_to)
        response.headers["X-Total-Count"] = str(c.store.count_events(**flt))
        return c.store.list_events(limit=limit, offset=offset, **flt)

    @router.get("/api/events.csv", dependencies=[Depends(guards.person())])
    def events_csv(
        camera: str | None = None,
        camera_id: str | None = None,
        type: str | None = None,
        zone: str | None = None,
        status: EventStatus | None = None,
        ts_from: Annotated[AwareDatetime | None, Query(alias="from")] = None,
        ts_to: Annotated[AwareDatetime | None, Query(alias="to")] = None,
    ) -> Response:
        flt = _filters(camera, camera_id, type, zone, status, ts_from, ts_to)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(CSV_COLUMNS)
        areas = {cam.id: cam.area or "" for cam in c.store.list_cameras()}

        def cell(value: object) -> object:
            """Ячейка CSV: текст, начинающийся с = + - @, Excel выполнил бы как формулу."""
            text = str(value)
            return "'" + text if text[:1] in ("=", "+", "-", "@") else value

        for e in c.store.list_events(limit=None, **flt):
            history = "; ".join(f"{h.ts.isoformat()} {h.status} {h.by}" for h in e.status_history)
            reason = next((h.reason for h in reversed(e.status_history) if h.reason), "")
            first = reaction(e)
            last = closure(e)
            seconds = round((first.ts - e.first_seen).total_seconds()) if first else ""
            writer.writerow(
                [
                    e.ts.isoformat(),
                    cell(e.camera_id),
                    cell(e.zone_id or ""),
                    cell(e.type),
                    e.status,
                    reason,
                    history,
                    e.id,
                    cell(areas.get(e.camera_id, "")),
                    e.count,
                    e.first_seen.isoformat(),
                    e.last_seen.isoformat() if e.last_seen else "",
                    cell(first.by) if first else "",
                    first.ts.isoformat() if first else "",
                    seconds,
                    e.snapshot_url or "",
                    cell(last.by) if last else "",
                    last.ts.isoformat() if last else "",
                ]
            )
        return Response(
            "﻿" + out.getvalue(),  # BOM: Excel открывает UTF-8
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="events.csv"'},
        )

    @router.post(
        "/api/events",
        status_code=201,
        response_model=Event,
        response_model_exclude_none=True,
        dependencies=[Depends(guards.worker)],
    )
    async def create_event(data: EventIn, background: BackgroundTasks) -> Event:
        event = c.events.ingest(data)
        if event.count == 1:  # повтор в окне только обновил инцидент: оповещение уже ушло
            background.add_task(c.events.after_ingest, event)
        return event

    @router.patch("/api/events/{event_id}", response_model=Event, response_model_exclude_none=True)
    async def set_status(event_id: str, update: StatusUpdate, user: Marker) -> Event:
        by = user.login if c.auth.enabled else (update.by or "operator")
        try:
            return await c.events.change_status(event_id, update.status, by, update.reason)
        except EventNotFound as exc:
            raise HTTPException(404, "событие не найдено") from exc
        except MasterOffline as exc:
            raise HTTPException(409, "мастер не в сети") from exc

    @router.get("/api/stream")
    async def stream(request: Request, user: Anyone) -> StreamingResponse:
        # Режим разработки без входа: любая вкладка считается мастером, чтобы работало демо
        is_master = user.role == "master" or not c.auth.enabled
        return StreamingResponse(
            event_stream(c.hub, request.is_disconnected, master=is_master, areas=user.areas),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
