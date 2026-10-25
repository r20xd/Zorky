"""Тела запросов, которых нет в контракте событий: вход, пароли, пользователи."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..domain.models import RuleCode, Schedule

Role = Literal["operator", "master", "specialist", "manager", "auditor", "admin"]


class LoginIn(BaseModel):
    model_config = {"extra": "forbid"}
    login: str
    password: str


class DemoPlayIn(BaseModel):
    model_config = {"extra": "forbid"}
    camera_id: str
    video: str
    name: str | None = None  # как назвать камеру, если её ещё нет
    detect: bool = False  # не крутить кадрами: источник камеры - файл, его читает воркер с моделью


class DemoStopIn(BaseModel):
    model_config = {"extra": "forbid"}
    camera_id: str


class LinqDeviceIn(BaseModel):
    model_config = {"extra": "forbid"}
    device: str


class DemoIn(BaseModel):
    model_config = {"extra": "forbid"}
    login: str


class PasswordIn(BaseModel):
    model_config = {"extra": "forbid"}
    old_password: str
    new_password: str = Field(min_length=10)


class UserCreateIn(BaseModel):
    model_config = {"extra": "forbid"}
    login: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    name: str = Field(min_length=1)
    role: Role
    areas: list[str] | None = None
    # свой пароль вместо сгенерированного; must_change_password — сменить при первом входе
    password: str | None = Field(default=None, min_length=10)
    must_change_password: bool = True


class AreaIn(BaseModel):
    model_config = {"extra": "forbid"}
    rules: list[RuleCode] | None = None
    rule_schedule: dict[RuleCode, Schedule] | None = None


class CameraPatchIn(BaseModel):
    """Что специалист меняет у камеры (адрес не трогает); чего нет в теле — не меняется."""

    model_config = {"extra": "forbid"}
    name: str | None = Field(default=None, min_length=1, pattern=r"\S")
    area: str | None = Field(default=None, max_length=64)  # "" или null — снять с участка
    rules: list[RuleCode] | None = None  # null — вернуть правила участка
    rule_schedule: dict[RuleCode, Schedule] | None = None

    @field_validator("name")
    @classmethod
    def name_not_null(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("название нельзя очистить")
        return value


class PasswordResetIn(BaseModel):
    model_config = {"extra": "forbid"}
    password: str | None = Field(default=None, min_length=10)
    must_change_password: bool = True


class UserPatchIn(BaseModel):
    model_config = {"extra": "forbid"}
    name: str | None = Field(default=None, min_length=1)
    role: Role | None = None
    active: bool | None = None
    areas: list[str] | None = None
