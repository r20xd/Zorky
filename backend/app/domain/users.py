"""Пользователи и пароли: сущность, роли и хеширование (только стандартная библиотека)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

ROLES = ("operator", "master", "specialist", "manager", "auditor", "admin")
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


@dataclass(frozen=True)
class User:
    login: str
    name: str
    role: str
    must_change_password: bool = False
    areas: tuple[str, ...] = ()  # участки мастера; пусто — видит всё


class UserError(Exception):
    """Ошибка управления пользователями: status — код ответа."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class InvalidCredentials(Exception):
    """Неверный логин или пароль (или пользователь отключён)."""


class TooManyAttempts(Exception):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)


def temp_password() -> str:
    return secrets.token_urlsafe(12)


def make_record(
    name: str,
    role: str,
    password: str,
    created_by: str = "cli",
    must_change_password: bool = False,
    areas: list[str] | None = None,
) -> dict:
    salt = secrets.token_bytes(16)
    return {
        **({"areas": list(areas)} if areas else {}),
        "name": name,
        "role": role,
        "salt": salt.hex(),
        "hash": hash_password(password, salt).hex(),
        "active": True,
        "must_change_password": must_change_password,
        "created_at": now_iso(),
        "created_by": created_by,
    }


def set_password(record: dict, password: str, must_change: bool) -> None:
    salt = secrets.token_bytes(16)
    record["salt"] = salt.hex()
    record["hash"] = hash_password(password, salt).hex()
    record["must_change_password"] = must_change


def check_password(record: dict, password: str) -> bool:
    got = hash_password(password, bytes.fromhex(record["salt"]))
    return hmac.compare_digest(got, bytes.fromhex(record["hash"]))


def user_from_record(login: str, record: dict) -> User:
    return User(
        login,
        record["name"],
        record["role"],
        bool(record.get("must_change_password")),
        tuple(record.get("areas") or ()),
    )


def public_user(login: str, record: dict) -> dict:
    """Пользователь для API: без соли и хеша."""
    return {
        "login": login,
        "name": record["name"],
        "role": record["role"],
        **({"areas": list(record["areas"])} if record.get("areas") else {}),
        "active": record.get("active", True),
        "created_at": record.get("created_at") or "1970-01-01T00:00:00Z",
        "created_by": record.get("created_by") or "cli",
    }
