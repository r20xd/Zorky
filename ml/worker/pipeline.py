"""Детекции кадра -> сработавшие нарушения. Без видео и модели, поэтому легко тестируется."""

from __future__ import annotations

from .rules import Debouncer, Detection, Rule, Violation, ZoneRule, build_rules
from .rules.geometry import zone_bounds

# люди, техника, телефоны
COCO_TRACKED = {0: "person", 2: "car", 5: "bus", 6: "train", 7: "truck", 67: "cell phone"}


def parse_tracks(
    xyxy: list[list[float]],
    confs: list[float],
    ids: list[int] | None,
    width: int,
    height: int,
    class_ids: list[int] | None = None,
    keypoints: list | None = None,
) -> list[Detection]:
    """Результат трекера YOLO (пиксели) -> люди и техника с bbox 0..1.

    Без id трека (ids=None) — пусто. class_ids — классы COCO; без них все рамки — люди.
    """
    if ids is None:
        return []
    people = []
    classes = class_ids if class_ids is not None else [0] * len(ids)
    poses = keypoints if keypoints is not None else [None] * len(ids)
    for (x1, y1, x2, y2), conf, track_id, class_id, pose in zip(
        xyxy, confs, ids, classes, poses, strict=True
    ):
        name = COCO_TRACKED.get(class_id)
        if name is None:
            continue
        box = (
            min(max(x1 / width, 0.0), 1.0),
            min(max(y1 / height, 0.0), 1.0),
            min(max(x2 / width, 0.0), 1.0),
            min(max(y2 / height, 0.0), 1.0),
        )
        kp = None
        if pose is not None:  # пиксели -> 0..1
            kp = tuple((px / width, py / height, pc) for px, py, pc in pose)
        aspect = width / height
        people.append(Detection(name, box, conf, track_id, frame_aspect=aspect, keypoints=kp))
    return people


PPE_CLASSES = ("helmet", "head", "respirator", "cigarette")  # person берём у модели людей
PPE_ALIASES = {"face_protection": "respirator"}  # ppe-v3: общий класс защиты лица


def parse_ppe(
    xyxy: list[list[float]],
    confs: list[float],
    class_ids: list[int],
    names: dict[int, str],
    width: int,
    height: int,
) -> list[Detection]:
    """Детекции модели СИЗ (пиксели) -> Detection с bbox 0..1, track_id=-1."""
    found = []
    for (x1, y1, x2, y2), conf, class_id in zip(xyxy, confs, class_ids, strict=True):
        name = names.get(class_id)
        name = PPE_ALIASES.get(name, name)
        if name not in PPE_CLASSES:
            continue
        box = (
            min(max(x1 / width, 0.0), 1.0),
            min(max(y1 / height, 0.0), 1.0),
            min(max(x2 / width, 0.0), 1.0),
            min(max(y2 / height, 0.0), 1.0),
        )
        found.append(Detection(name, box, conf, track_id=-1))
    return found


def find_violations(
    detections: list[Detection],
    zones: list[dict],
    debouncer: Debouncer,
    now: float,
    rules: list[Rule] | None = None,
) -> list[Violation]:
    """Прогнать людей кадра через правила камеры (по умолчанию — все из реестра)."""
    if rules is None:
        rules = build_rules("")
    for rule in rules:
        rule.on_frame(now)
    fired = []
    people = [d for d in detections if d.cls == "person"]
    for rule in rules:
        if not isinstance(rule, ZoneRule):
            continue
        verdicts = rule.check_frame(detections if rule.wants_detections else people, zones)
        for zv in verdicts:
            zone_id = str(zv.zone["id"]) if "id" in zv.zone else None
            obj = zv.detection
            key = zone_id if obj is None else f"{zone_id or zv.zone.get('key')}:{obj.track_id}"
            if debouncer.update(key, rule.name, zv.active, now):
                if obj is None:
                    obj = Detection("zone", zone_bounds(zv.zone), 1.0, -1)
                fired.append(Violation(rule.name, obj, zone_id, zv.severity))
    for detection in people:
        for rule in rules:
            if isinstance(rule, ZoneRule):
                continue
            verdict = rule.check(detection, detections, zones)
            if debouncer.update(detection.track_id, rule.name, verdict.active, now):
                fired.append(Violation(rule.name, detection, verdict.zone_id, verdict.severity))
    return fired
