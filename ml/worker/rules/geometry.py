"""Геометрия правил: ноги человека, точка в полигоне, зона, область головы."""

from __future__ import annotations

Point = tuple[float, float]
BBox = tuple[float, float, float, float]  # x1, y1, x2, y2 — нормированные 0..1


def foot_point(bbox: BBox) -> Point:
    """Середина нижней кромки рамки — «ноги» человека. По ней проверяем вход в зону."""
    x1, _, x2, y2 = bbox
    return ((x1 + x2) / 2, y2)


def point_in_polygon(point: Point, polygon: list[Point]) -> bool:
    """Лучевой алгоритм: нечётное число пересечений луча вправо с рёбрами — точка внутри."""
    x, y = point
    inside = False
    for i, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[(i + 1) % len(polygon)]
        if (y1 > y) != (y2 > y):
            x_cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_cross:
                inside = not inside
    return inside


def zone_hit(foot: Point, zones: list[dict], kind: str) -> dict | None:
    """Первая зона нужного вида, в которой стоит точка."""
    for zone in zones:
        polygon = [tuple(p) for p in zone["polygon"]]
        if zone.get("kind") == kind and point_in_polygon(foot, polygon):
            return zone
    return None


HEAD_PART = 0.4  # верхняя доля рамки человека, где ищем голову и каску


def in_head_area(person: BBox, item: BBox, head_part: float = HEAD_PART) -> bool:
    """Центр рамки item лежит в верхней части рамки человека."""
    x1, y1, x2, y2 = person
    cx, cy = (item[0] + item[2]) / 2, (item[1] + item[3]) / 2
    return x1 <= cx <= x2 and y1 <= cy <= y1 + (y2 - y1) * head_part


def zone_bounds(zone: dict) -> BBox:
    """Границы полигона зоны: [x1, y1, x2, y2] в 0..1."""
    xs = [p[0] for p in zone["polygon"]]
    ys = [p[1] for p in zone["polygon"]]
    return (min(xs), min(ys), max(xs), max(ys))


def people_in_zone(people: list, zone: dict) -> list:
    """Люди (Detection), чьи ноги стоят в полигоне зоны."""
    polygon = [tuple(p) for p in zone["polygon"]]
    return [p for p in people if point_in_polygon(foot_point(p.bbox), polygon)]
