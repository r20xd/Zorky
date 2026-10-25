"""Постоянное хранилище в одном файле SQLite (путь ":memory:" — только для тестов).

Событие, камера и зоны лежат как JSON строкой; отдельно — поля для фильтров.
Одно соединение на все потоки под замком: нагрузка маленькая, так проще всего.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from ..domain.incidents import matches, new_incident, paginate, repeat, same_incident, with_status
from ..domain.models import Area, Camera, Event, EventIn, EventStatus, Zone
from . import config


class SqliteStore:
    def __init__(self, path: str | Path = ":memory:", window_s: float | None = None) -> None:
        self._window_s = config.incident_window_s() if window_s is None else window_s
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    n INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT UNIQUE,
                    camera_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cameras (
                    pos INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT UNIQUE,
                    body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS zones (
                    camera_id TEXT PRIMARY KEY,
                    body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS areas (
                    pos INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE,
                    body TEXT NOT NULL
                );
                """
            )

    def add_event(self, data: EventIn) -> Event:
        with self._lock, self._db:
            rows = self._db.execute(
                "SELECT n, body FROM events WHERE camera_id = ? AND type = ? "
                "ORDER BY n DESC LIMIT 200",
                (data.camera_id, data.type),
            ).fetchall()
            for n, body in rows:
                old = Event.model_validate_json(body)
                if same_incident(old, data, self._window_s):
                    merged = repeat(old, data, self._window_s)
                    if merged:
                        self._db.execute(
                            "UPDATE events SET body = ? WHERE n = ?", (merged.model_dump_json(), n)
                        )
                        return merged
            n = self._db.execute(
                "INSERT INTO events (camera_id, type, body) VALUES (?, ?, '')",
                (data.camera_id, data.type),
            ).lastrowid
            event = new_incident(data, f"evt-{n:06d}")
            self._db.execute(
                "UPDATE events SET id = ?, body = ? WHERE n = ?",
                (event.id, event.model_dump_json(), n),
            )
        return event

    def get_event(self, event_id: str) -> Event | None:
        with self._lock:
            row = self._db.execute("SELECT body FROM events WHERE id = ?", (event_id,)).fetchone()
        return Event.model_validate_json(row[0]) if row else None

    def list_events(
        self,
        limit: int | None = 100,
        camera_id: str | None = None,
        type: str | None = None,
        zone_id: str | None = None,
        status: str | None = None,
        ts_from: datetime | None = None,
        ts_to: datetime | None = None,
        offset: int = 0,
    ) -> list[Event]:
        """Новые сверху; limit=None — без ограничения."""
        found = self._filtered(camera_id, type, zone_id, status, ts_from, ts_to)
        return paginate(found, limit, offset)

    def _filtered(self, camera_id, type, zone_id, status, ts_from, ts_to) -> list[Event]:
        # камера и тип — в SQL, остальное — по разобранному событию
        where, args = [], []
        if camera_id:
            where.append("camera_id = ?")
            args.append(camera_id)
        if type:
            where.append("type = ?")
            args.append(type)
        sql = "SELECT body FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY n DESC"
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        events = (Event.model_validate_json(body) for (body,) in rows)
        return [e for e in events if matches(e, None, None, zone_id, status, ts_from, ts_to)]

    def count_events(
        self,
        camera_id: str | None = None,
        type: str | None = None,
        zone_id: str | None = None,
        status: str | None = None,
        ts_from: datetime | None = None,
        ts_to: datetime | None = None,
    ) -> int:
        return len(self._filtered(camera_id, type, zone_id, status, ts_from, ts_to))

    def set_status(
        self, event_id: str, status: EventStatus, by: str = "operator", reason: str | None = None
    ) -> Event | None:
        with self._lock, self._db:
            row = self._db.execute("SELECT body FROM events WHERE id = ?", (event_id,)).fetchone()
            if row is None:
                return None
            event = with_status(Event.model_validate_json(row[0]), status, by, reason)
            self._db.execute(
                "UPDATE events SET body = ? WHERE id = ?", (event.model_dump_json(), event_id)
            )
        return event

    def list_cameras(self) -> list[Camera]:
        with self._lock:
            rows = self._db.execute("SELECT body FROM cameras ORDER BY pos").fetchall()
        return [Camera.model_validate_json(body) for (body,) in rows]

    def upsert_camera(self, camera: Camera) -> Camera:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO cameras (id, body) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET body = excluded.body",
                (camera.id, camera.model_dump_json()),
            )
        return camera

    def delete_camera(self, camera_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
            self._db.execute("DELETE FROM zones WHERE camera_id = ?", (camera_id,))

    def list_areas(self) -> list[Area]:
        with self._lock:
            rows = self._db.execute("SELECT body FROM areas ORDER BY pos").fetchall()
        return [Area.model_validate_json(body) for (body,) in rows]

    def upsert_area(self, area: Area) -> Area:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO areas (name, body) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET body = excluded.body",
                (area.name, area.model_dump_json()),
            )
        return area

    def delete_area(self, name: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM areas WHERE name = ?", (name,))

    def zones(self, camera_id: str) -> list[Zone]:
        with self._lock:
            row = self._db.execute(
                "SELECT body FROM zones WHERE camera_id = ?", (camera_id,)
            ).fetchone()
        return [Zone.model_validate(item) for item in json.loads(row[0])] if row else []

    def set_zones(self, camera_id: str, zones: list[Zone]) -> list[Zone]:
        body = json.dumps([zone.model_dump(mode="json") for zone in zones])
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO zones (camera_id, body) VALUES (?, ?) "
                "ON CONFLICT(camera_id) DO UPDATE SET body = excluded.body",
                (camera_id, body),
            )
        return self.zones(camera_id)
