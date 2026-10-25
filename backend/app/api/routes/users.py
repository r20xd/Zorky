"""Управление пользователями — только admin."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from ...domain.users import User, UserError
from ..container import Container
from ..schemas import PasswordResetIn, UserCreateIn, UserPatchIn


def build_router(c: Container) -> APIRouter:
    router = APIRouter()
    auth = c.auth
    AdminUser = Annotated[User, Depends(c.guards.person("admin"))]

    def users_call(fn, *args):
        if not auth.enabled:
            raise HTTPException(403, "режим разработки: пользователей нет")
        try:
            return fn(*args)
        except UserError as exc:
            raise HTTPException(exc.status, exc.message) from exc

    @router.get("/api/users")
    def list_users(_: AdminUser) -> list[dict]:
        return users_call(auth.list_users)

    @router.post("/api/users", status_code=201)
    def create_user(data: UserCreateIn, admin: AdminUser) -> dict:
        user, generated = users_call(
            auth.create_user,
            admin.login,
            data.login,
            data.name,
            data.role,
            data.areas,
            data.password,
            data.must_change_password,
        )
        return {"user": user, "temp_password": generated}

    @router.patch("/api/users/{login}")
    def patch_user(login: str, data: UserPatchIn, admin: AdminUser) -> dict:
        changes = data.model_dump(exclude_none=True)
        if not changes:
            raise HTTPException(400, "нужно хотя бы одно поле")
        return users_call(auth.update_user, admin.login, login, changes)

    @router.post("/api/users/{login}/reset-password")
    def reset_password(login: str, admin: AdminUser, data: PasswordResetIn | None = None) -> dict:
        data = data or PasswordResetIn()
        generated = users_call(
            auth.reset_password, admin.login, login, data.password, data.must_change_password
        )
        return {"temp_password": generated}

    return router
