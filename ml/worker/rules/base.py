"""Общий интерфейс правил, реестр по имени и сборка набора правил камеры из rules.yaml."""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from pathlib import Path

from .geometry import BBox

CONFIG_PATH = Path(__file__).resolve().parent.parent / "rules.yaml"


@dataclass(frozen=True)
class Detection:
    cls: str  # person, helmet, head, respirator, cigarette — классы из ml/train/data.yaml
    bbox: BBox  # нормированные 0..1
    confidence: float
    track_id: int
    frame_aspect: float = 1.0  # ширина/высота кадра для физических пропорций bbox
    keypoints: tuple | None = None  # поза COCO-17: (x, y, conf) на точку, x/y нормированы 0..1


@dataclass(frozen=True)
class Violation:
    type: str  # как поле type в contracts/events.schema.json
    detection: Detection
    zone_id: str | None = None
    severity: str | None = None


@dataclass(frozen=True)
class Verdict:
    """Ответ правила для одного человека на кадре."""

    active: bool
    zone_id: str | None = None
    severity: str | None = None


class Rule:
    """Правило нарушения. Наследник задаёт name (= type события) и check().

    Параметры из rules.yaml приходят в __init__ как именованные аргументы.
    """

    name: str = ""

    def __init__(self, **params) -> None:
        if params:
            raise TypeError(f"правило {self.name}: неизвестные параметры {sorted(params)}")

    def on_frame(self, now: float) -> None:
        """Необязательный hook времени кадра; старым правилам ничего делать не нужно."""

    def check(self, person: Detection, detections: list[Detection], zones: list[dict]) -> Verdict:
        raise NotImplementedError


@dataclass(frozen=True)
class ZoneVerdict:
    """Ответ правила уровня зоны: одна зона, активно ли нарушение."""

    zone: dict
    active: bool
    severity: str | None = None
    detection: Detection | None = None  # объект нарушения (рамка и трек); нет — границы зоны


class ZoneRule(Rule):
    """Правило уровня кадра: видит всех людей и все зоны сразу, отвечает по зонам.

    Наследник задаёт name и check_frame(). Событие — без трека, bbox = границы полигона зоны,
    антидребезг по паре (zone_id, name). Время кадра приходит в on_frame(now).
    """

    def check(self, person: Detection, detections: list[Detection], zones: list[dict]) -> Verdict:
        return Verdict(False)  # на людей по одному не смотрит

    wants_detections = False  # True: check_frame получает все детекции кадра, не только людей

    def check_frame(self, people: list[Detection], zones: list[dict]) -> list[ZoneVerdict]:
        raise NotImplementedError


REGISTRY: dict[str, type[Rule]] = {}


def register(cls: type[Rule]) -> type[Rule]:
    """Декоратор: записать правило в реестр под cls.name."""
    if not cls.name:
        raise ValueError(f"{cls.__name__}: пустое name")
    if REGISTRY.get(cls.name, cls) is not cls:
        raise ValueError(f"правило {cls.name} уже зарегистрировано")
    REGISTRY[cls.name] = cls
    return cls


def discover() -> None:
    """Импортировать все модули пакета rules — каждый регистрирует свои правила."""
    package = importlib.import_module(__package__)
    for module in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{__package__}.{module.name}")


def load_config(path: Path | str | None = None) -> dict:
    """rules.yaml -> словарь; нет файла — пустой (тогда у всех камер все правила)."""
    import yaml

    path = Path(path) if path else CONFIG_PATH
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


POSE_RULES = frozenset({"distress_signal"})  # правила, которым нужны ключевые точки


def needs_pose(camera_id: str, config: dict | None = None) -> bool:
    """Включено ли для камеры правило по позе (тогда воркер грузит модель позы)."""
    return any(rule.name in POSE_RULES for rule in build_rules(camera_id, config))


def _params(name: str, camera_id: str, config: dict) -> dict:
    """Параметры правила: у камеры, иначе в default, иначе в optional (выключенные по умолчанию)."""
    for section in (
        (config.get("cameras") or {}).get(camera_id),
        config.get("default"),
        config.get("optional"),
    ):
        if section and name in section:
            return section[name] or {}
    return {}


def build_rules(
    camera_id: str, config: dict | None = None, enabled: list[str] | None = None
) -> list[Rule]:
    """Правила камеры: config["cameras"][camera_id], иначе config["default"], иначе все
    зарегистрированные без параметров. Значение — словарь «имя правила -> параметры».

    enabled — выбор специалиста по охране труда из бэкенда: ровно эти правила, параметры из
    rules.yaml (камера, default, optional); неизвестные имена пропускаются."""
    discover()
    config = load_config() if config is None else config
    if enabled is not None:
        return [
            REGISTRY[name](**_params(name, camera_id, config))
            for name in dict.fromkeys(enabled)
            if name in REGISTRY
        ]
    spec = (config.get("cameras") or {}).get(camera_id)
    if spec is None:
        spec = config.get("default")
    if spec is None:
        spec = {name: {} for name in REGISTRY}
    rules = []
    for name, params in spec.items():
        if name not in REGISTRY:
            raise ValueError(f"rules.yaml: неизвестное правило {name!r}; есть {sorted(REGISTRY)}")
        rules.append(REGISTRY[name](**(params or {})))
    return rules
