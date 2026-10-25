"""Вход, выход, «кто я», смена своего пароля."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ...application.auth_service import DEV_USER, SESSION_TTL_S
from ...domain.users import InvalidCredentials, TooManyAttempts, User, UserError
from ...infrastructure import config
from ..container import Container
from ..guards import COOKIE
from ..schemas import DemoIn, LoginIn, PasswordIn


def me_json(user: User) -> dict:
    data = {"login": user.login, "name": user.name, "role": user.role}
    if user.must_change_password:
        data["must_change_password"] = True
    return data


def build_router(c: Container) -> APIRouter:
    router = APIRouter()
    auth = c.auth
    AnyoneTemp = Annotated[User, Depends(c.guards.person(allow_temp=True))]

    def session_response(user: User, token: str, request: Request) -> JSONResponse:
        response = JSONResponse(me_json(user))
        secure = (
            request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"
        )
        response.set_cookie(
            COOKIE,
            token,
            max_age=SESSION_TTL_S,
            httponly=True,
            samesite="strict",
            secure=secure,
            path="/",
        )
        return response

    @router.get("/api/auth/demo")
    def demo_users() -> list[dict]:
        """Роли для входа в один клик; вне демо-режима список пуст."""
        if not config.demo_mode():
            return []
        return [
            {"login": login, "name": rec["name"], "role": rec["role"]}
            for login, rec in auth.demo_users()
        ]

    @router.post("/api/auth/demo")
    def demo_login(data: DemoIn, request: Request) -> JSONResponse:
        if not config.demo_mode():
            raise HTTPException(404, "демо-режим выключен")
        try:
            user, token = auth.demo_login(data.login)
        except InvalidCredentials as exc:
            raise HTTPException(404, str(exc)) from exc
        return session_response(user, token, request)

    @router.post("/api/auth/login")
    def login(data: LoginIn, request: Request) -> JSONResponse:
        if not auth.enabled:
            return JSONResponse(me_json(DEV_USER))
        try:
            user, token = auth.login(data.login, data.password)
        except InvalidCredentials as exc:
            raise HTTPException(401, str(exc)) from exc
        except TooManyAttempts as exc:
            return JSONResponse(
                status_code=429,
                content={"error": "слишком много попыток входа"},
                headers={"Retry-After": str(exc.retry_after)},
            )
        return session_response(user, token, request)

    @router.post("/api/auth/logout", status_code=204)
    def logout(request: Request, _: AnyoneTemp) -> Response:
        auth.logout(request.cookies.get(COOKIE))
        response = Response(status_code=204)
        response.delete_cookie(COOKIE, path="/")
        return response

    @router.get("/api/auth/me")
    def me(user: AnyoneTemp) -> dict:
        return me_json(user)

    @router.post("/api/auth/password", status_code=204)
    def change_password(data: PasswordIn, user: AnyoneTemp) -> Response:
        if not auth.enabled:
            raise HTTPException(403, "режим разработки: пользователей нет")
        try:
            auth.change_password(user.login, data.old_password, data.new_password)
        except UserError as exc:
            raise HTTPException(exc.status, exc.message) from exc
        return Response(status_code=204)

    return router
