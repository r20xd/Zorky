"""Сущности и типы контракта contracts/openapi.yaml. Меняются только вместе с контрактом."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

_LEGACY_STATUS = {"acknowledged": "confirmed", "false_positive": "false"}
FalseReason = Literal["not_person", "ppe_present", "wrong_zone", "duplicate", "other"]
EventStatus = Annotated[
    Literal["new", "confirmed", "sent_to_master", "resolved", "false"],
    BeforeValidator(lambda v: _LEGACY_STATUS.get(v, v) if isinstance(v, str) else v),
]
ZoneKind = Literal[
    "machinery",
    "fence",
    "height",
    "other",
    "danger",
    "flammable",
    "under_load",
    "two_person",
    "crowd_limit",
]
Unit = Annotated[float, Field(ge=0, le=1)]  # координата, нормированная к размеру кадра
RuleCode = Annotated[str, Field(pattern=r"^[a-z][a-z_]*$")]  # код типа нарушения из реестра
ClockTime = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]  # ЧЧ:ММ


class Strict(BaseModel):
    """Лишние поля — ошибка: опечатка в имени поля не должна молча теряться."""

    model_config = ConfigDict(extra="forbid")


class EventIn(Strict):
    """Событие от ML-воркера — contracts/events.schema.json."""

    camera_id: str = Field(min_length=1)
    ts: AwareDatetime
    frame_id: int = Field(ge=0)
    type: str = Field(min_length=1)  # код из реестра нарушений
    bbox: list[Unit] = Field(min_length=4, max_length=4)
    confidence: float = Field(ge=0, le=1)
    track_id: int | None = Field(default=None, ge=0)
    snapshot_url: str | None = None
    zone_id: str | None = None
    severity: str | None = None
    # Инцидент: заполняет бэкенд; воркер может не присылать
    incident_id: str | None = None
    count: int | None = Field(default=None, ge=1)
    first_seen: AwareDatetime | None = None
    last_seen: AwareDatetime | None = None

    @field_validator("bbox")
    @classmethod
    def corners_in_order(cls, bbox: list[float]) -> list[float]:
        # Заодно ловит [x, y, w, h] вместо [x1, y1, x2, y2]
        if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
            raise ValueError("нужно x1 < x2 и y1 < y2")
        return bbox


class StatusChange(Strict):
    status: EventStatus
    ts: AwareDatetime
    by: str = "system"  # кто сменил: operator, telegram:<user>, master, system
    reason: FalseReason | None = None


class Event(EventIn):
    """Событие в журнале: EventIn и поля, которые проставляет бэкенд."""

    id: str
    status: EventStatus = "new"
    received_at: datetime
    # Повторы одного нарушения склеиваются в один инцидент (id инцидента = id события)
    count: int = Field(default=1, ge=1)
    status_history: list[StatusChange] = Field(default_factory=list)


class StatusUpdate(Strict):
    status: EventStatus
    by: str | None = None
    reason: FalseReason | None = None

    @model_validator(mode="after")
    def reason_only_for_false(self) -> "StatusUpdate":
        if self.reason is not None and self.status != "false":
            raise ValueError("reason допустима только со статусом false")
        return self


class Metrics(Strict):
    """Метрики инференса от воркера — Metrics в contracts/openapi.yaml."""

    camera_id: str = Field(min_length=1)
    device: Literal["linq", "cpu", "gpu"]
    fps: float = Field(ge=0)
    inference_ms: float = Field(ge=0)
    e2e_ms: float | None = Field(default=None, ge=0)
    ts: AwareDatetime


class Schedule(Strict):
    """Когда зона действует (местное время площадки); start > end — через полночь."""

    start: ClockTime
    end: ClockTime


class Camera(Strict):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1, pattern=r"\S")  # не пустое и не из одних пробелов
    source: str | None = None  # RTSP-адрес, путь к ролику или номер веб-камеры (0, 1…) у воркера
    area: str | None = Field(default=None, max_length=64)  # участок (для передачи мастеру)
    # какие нарушения контролировать на этой камере; нет — как на участке, а там нет — по умолчанию
    rules: list[RuleCode] | None = None
    # когда действует каждое правило (местное время); нет записи — всегда
    rule_schedule: dict[RuleCode, Schedule] | None = None


class Area(Strict):
    """Участок: общие правила для его камер; камера может переопределить их у себя."""

    name: str = Field(min_length=1, max_length=64)
    rules: list[RuleCode] | None = None
    rule_schedule: dict[RuleCode, Schedule] | None = None


class Zone(Strict):
    """Полигон на кадре камеры: danger — вход запрещён, flammable — рядом горючее."""

    id: str = Field(min_length=1)
    kind: ZoneKind
    name: str | None = None
    polygon: list[tuple[Unit, Unit]] = Field(min_length=3)
    schedule: Schedule | None = None  # нет — действует всегда
