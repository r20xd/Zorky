"""Кто может вызвать маршрут: вошедший человек (cookie), воркер (Bearer-токен), свой Origin."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from ..application.auth_service import DEV_USER, AuthService
from ..domain.users import User

COOKIE = "session"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def check_origin(request: Request) -> None:
    """Мутирующий запрос с чужим Origin — 403 (CSRF поверх SameSite)."""
    origin = request.headers.get("origin")
    if request.method not in MUTATING or not origin:
        return
    if urlsplit(origin).netloc != request.headers.get("host", ""):
        raise HTTPException(403, "чужой Origin")


class Guards:
    def __init__(self, auth: AuthService) -> None:
        self._auth = auth

    def origin_guard(self, request: Request) -> None:
        if self._auth.enabled:
            check_origin(request)

    def is_worker(self, request: Request) -> bool:
        return self._auth.worker_ok(request.headers.get("authorization", ""))

    def session_user(self, request: Request) -> User | None:
        return self._auth.user_by_token(request.cookies.get(COOKIE))

    def person(self, *roles: str, allow_temp: bool = False):
        """Вошедший человек (cookie); roles — допустимые роли, пусто — любая.

        Пока пароль временный (must_change_password) — 403, кроме allow_temp."""

        def dep(request: Request) -> User:
            if not self._auth.enabled:
                return DEV_USER
            user = self.session_user(request)
            if user is None:
                raise HTTPException(401, "нужен вход")
            if user.must_change_password and not allow_temp:
                raise HTTPException(403, "смените пароль")
            if roles and user.role not in roles:
                raise HTTPException(403, "роль не разрешает это действие")
            return user

        return dep

    def worker(self, request: Request) -> None:
        if self._auth.enabled and not self.is_worker(request):
            raise HTTPException(401, "нужен токен воркера")

    def person_or_worker(self, request: Request) -> None:
        if self._auth.enabled and not self.is_worker(request):
            self.person()(request)
