"""Сессии входа в SQLite: токен в базе не хранится, только его хеш."""

from __future__ import annotations

import sqlite3
import threading
import time


class SqliteSessionStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS sessions "
            "(token_hash TEXT PRIMARY KEY, login TEXT NOT NULL, expires REAL NOT NULL)"
        )

    def add(self, token_hash: str, login: str, expires: float) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
            self._db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (token_hash, login, expires))

    def find(self, token_hash: str) -> tuple[str, float] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT login, expires FROM sessions WHERE token_hash = ?", (token_hash,)
            ).fetchone()
        return None if row is None else (row[0], row[1])

    def delete(self, token_hash: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def delete_for_login(self, login: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM sessions WHERE login = ?", (login,))
