"""Вход и роли: сессии (в хранилище только хеш), перебор пароля, управление пользователями."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager

from ..domain.users import (
    InvalidCredentials,
    TooManyAttempts,
    User,
    UserError,
    check_password,
    make_record,
    public_user,
    set_password,
    temp_password,
    user_from_record,
)
from .ports import AuditLog, SessionStore, UserRepository

log = logging.getLogger("mosprom.auth")

SESSION_TTL_S = 12 * 3600
MAX_FAILS = 5
FAIL_WINDOW_S = 300
# Режим разработки (нет ни пользователей, ни токена воркера): вход не нужен, все действия открыты
DEV_USER = User(login="dev", name="Режим разработки", role="operator")


class AuthService:
    def __init__(
        self,
        users: UserRepository,
        sessions: SessionStore,
        audit: AuditLog,
        worker_token: str | None = None,
    ) -> None:
        self.users = users
        self.worker_token = worker_token
        self._sessions = sessions
        self._audit = audit
        self._fails: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._file_lock = threading.RLock()
        if not self.enabled:
            log.warning(
                "РЕЖИМ РАЗРАБОТКИ: нет файла пользователей (%s) и токена воркера — "
                "API открыт без входа",
                self.users.location,
            )

    @property
    def enabled(self) -> bool:
        return bool(self.users.current()) or bool(self.worker_token)

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def login(self, login: str, password: str) -> tuple[User, str]:
        now = time.monotonic()
        with self._lock:
            fails = self._fails[login]
            while fails and now - fails[0] > FAIL_WINDOW_S:
                fails.popleft()
            if len(fails) >= MAX_FAILS:
                raise TooManyAttempts(int(FAIL_WINDOW_S - (now - fails[0])) + 1)
        record = self.users.current().get(login)
        if record is None:  # то же время ответа, что и для неверного пароля
            check_password({"salt": "00", "hash": "00" * 32}, password)
        if record is None or not check_password(record, password) or not record.get("active", True):
            with self._lock:
                self._fails[login].append(now)
            raise InvalidCredentials("неверный логин или пароль")
        with self._lock:
            self._fails.pop(login, None)
        token = secrets.token_urlsafe(32)
        self._sessions.add(self._hash(token), login, time.time() + SESSION_TTL_S)
        return user_from_record(login, record), token

    def demo_users(self) -> list[tuple[str, dict]]:
        """Пользователи, созданные демо (created_by == demo): им разрешён вход без пароля."""
        return [
            (login, rec)
            for login, rec in self.users.current().items()
            if rec.get("created_by") == "demo" and rec.get("active", True)
        ]

    def demo_login(self, login: str) -> tuple[User, str]:
        record = dict(self.demo_users()).get(login)
        if record is None:
            raise InvalidCredentials("нет такого демо-пользователя")
        token = secrets.token_urlsafe(32)
        self._sessions.add(self._hash(token), login, time.time() + SESSION_TTL_S)
        self._audit.record(login, "demo_login", login)
        return user_from_record(login, record), token

    def user_by_token(self, token: str | None) -> User | None:
        if not token:
            return None
        found = self._sessions.find(self._hash(token))
        if found is None or found[1] < time.time():
            return None
        record = self.users.current().get(found[0])
        if not record or not record.get("active", True):
            return None
        return user_from_record(found[0], record)

    def logout(self, token: str | None) -> None:
        if token:
            self._sessions.delete(self._hash(token))

    def worker_ok(self, authorization: str) -> bool:
        """Заголовок Authorization: Bearer <токен воркера>."""
        scheme, _, given = authorization.partition(" ")
        if not self.worker_token or scheme.lower() != "bearer":
            return False
        return hmac.compare_digest(given.encode(), self.worker_token.encode())

    # --- управление пользователями ---

    @contextmanager
    def _users_lock(self):
        """Чтение-правка-запись набора пользователей под одним замком."""
        with self._file_lock:
            yield

    def list_users(self) -> list[dict]:
        return [public_user(login, rec) for login, rec in self.users.current().items()]

    def create_user(
        self,
        actor: str,
        login: str,
        name: str,
        role: str,
        areas: list[str] | None = None,
        password: str | None = None,
        must_change: bool = True,
    ) -> tuple[dict, str | None]:
        """Второе значение — сгенерированный пароль; None, если админ задал пароль сам."""
        with self._users_lock():
            current = self.users.current()
            if login in current:
                raise UserError(409, "такой login уже есть")
            generated = None if password else temp_password()
            users = dict(current)
            users[login] = make_record(
                name,
                role,
                password or generated,
                actor,
                must_change_password=must_change if password else True,
                areas=areas,
            )
            self.users.save(users)
            self._audit.record(actor, "user_create", login, role=role, own_password=bool(password))
            return public_user(login, users[login]), generated

    def update_user(self, actor: str, login: str, changes: dict) -> dict:
        with self._users_lock():
            current = self.users.current()
            old = current.get(login)
            if old is None:
                raise UserError(404, "нет такого пользователя")
            new = {**old, **changes}
            was_admin = old["role"] == "admin" and old.get("active", True)
            still_admin = new["role"] == "admin" and new.get("active", True)
            if was_admin and not still_admin:
                others = [
                    k
                    for k, r in current.items()
                    if k != login and r["role"] == "admin" and r.get("active", True)
                ]
                if not others:
                    raise UserError(409, "это последний активный admin")
            users = dict(current)
            users[login] = new
            self.users.save(users)
            diff = {k: v for k, v in changes.items() if old.get(k) != v}
            self._audit.record(actor, "user_update", login, changes=diff)
            if "role" in diff or new.get("active", True) is False:
                self._sessions.delete_for_login(login)
            return public_user(login, new)

    def reset_password(
        self, actor: str, login: str, password: str | None = None, must_change: bool = True
    ) -> str | None:
        """Новый пароль: свой от админа или сгенерированный (его и возвращаем)."""
        with self._users_lock():
            current = self.users.current()
            if login not in current:
                raise UserError(404, "нет такого пользователя")
            generated = None if password else temp_password()
            users = dict(current)
            users[login] = dict(users[login])
            set_password(users[login], password or generated, must_change if password else True)
            self.users.save(users)
            self._audit.record(actor, "password_reset", login, own_password=bool(password))
            self._sessions.delete_for_login(login)
            return generated

    def change_password(self, login: str, old: str, new: str) -> None:
        with self._users_lock():
            current = self.users.current()
            record = current.get(login)
            if record is None or not check_password(record, old):
                raise UserError(403, "неверный old_password")
            if new == old:
                raise UserError(400, "новый пароль равен старому")
            users = dict(current)
            users[login] = dict(record)
            set_password(users[login], new, False)
            self.users.save(users)
            self._audit.record(login, "password_change", login)
