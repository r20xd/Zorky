"""Эталонные треки синтетического склада NVIDIA (формат MTMC_Tracking_2025) и метки по ним."""

import math

from eval import nvidia_sim as ns

# камера смотрит строго вниз: u = 100 * x + 960, v = 100 * y + 540 (cameraMatrix 3x4, w = 1)
MATRIX = [[100, 0, 0, 960], [0, 100, 0, 540], [0, 0, 0, 1]]


def obj(typ, oid, x, y, cams, size=(0.6, 0.4, 1.8), yaw=0.0):
    return {
        "object type": typ,
        "object id": oid,
        "3d location": [x, y, 0.9],
        "3d bounding box scale": list(size),
        "3d bounding box rotation": [0.0, 0.0, yaw],
        "2d bounding box visible": {c: [0, 0, 10, 10] for c in cams},
    }


def gt(frames=90):
    """Человек 1 идёт по x от 0 до 4.5 м за 3 с (1.5 м/с) мимо стоящего погрузчика у x = 3."""
    out = {}
    for f in range(frames):
        out[str(f)] = [
            obj("Person", 1, f * 0.05, 0.0, ["Camera"]),
            obj("Forklift", 9, 3.0, 1.5, ["Camera"], size=(1.2, 2.3, 2.1)),
        ]
    return out


def test_parse_tracks():
    tr = ns.parse_tracks(gt(3))
    assert set(tr) == {("Person", 1), ("Forklift", 9)}
    assert tr[("Person", 1)][2]["xy"] == (0.1, 0.0)
    assert tr[("Forklift", 9)][0]["size"] == (1.2, 2.3)
    assert "Camera" in tr[("Person", 1)][0]["cams"]


def test_rect_gap_and_yaw():
    assert ns.rect_gap((0, 0), (0, 0), (2, 4), 0) == 0
    assert math.isclose(ns.rect_gap((2, 0), (0, 0), (2, 4), 0), 1.0)
    # прямоугольник повёрнут на 90 градусов: длинная сторона теперь вдоль x
    assert ns.rect_gap((2, 0), (0, 0), (2, 4), math.pi / 2) == 0


def test_speed_ms():
    tr = ns.parse_tracks(gt())[("Person", 1)]
    assert math.isclose(ns.speed_ms(tr, 45), 1.5)
    assert ns.speed_ms(tr, 3) is None  # рядом нет трека


def test_to_intervals_merge_and_min():
    flags = [(f, f in range(10, 60) or f in range(70, 80) or f == 200) for f in range(300)]
    assert ns.to_intervals(flags) == [
        (10 / 30, 79 / 30)
    ]  # провал 10 кадров склеен, одиночный кадр отброшен


def test_labels_proximity_and_running():
    tracks = ns.parse_tracks(gt())
    labs = ns.person_labels(tracks, "Camera")
    kinds = {x["type"] for x in labs}
    assert "vehicle_proximity" in kinds and "running" in kinds
    prox = next(x for x in labs if x["type"] == "vehicle_proximity")
    # зазор до погрузчика < 1 м, пока человек рядом с ним: середина ролика
    assert 0.0 < prox["start_s"] < 2.0 < prox["end_s"] <= 3.0
    assert ns.person_labels(tracks, "Camera_01") == []  # в другой камере объектов нет


def test_zone_polygons_and_labels():
    tracks = ns.parse_tracks(gt())
    zone = ns.square((2.0, 0.0), 2.0)
    labs = ns.person_labels(tracks, "Camera", danger=zone)
    dz = next(x for x in labs if x["type"] == "danger_zone")
    assert 0.5 < dz["start_s"] < 1.0 and 1.5 < dz["end_s"] < 2.0  # x от 1 до 3 м -> 0.67..2.0 с


def test_zone_for_camera_normalized_and_clipped():
    z = ns.zone_for_camera(MATRIX, ns.square((0.0, 0.0), 2.0), "z", "danger")
    assert z["kind"] == "danger"
    xs = [p[0] for p in z["polygon"]]
    assert math.isclose(min(xs), (960 - 100) / 1920, abs_tol=1e-3)
    # полигон наполовину за кадром отсекается по 0..1
    far = ns.zone_for_camera(MATRIX, ns.square((9.6, 0.0), 2.0), "z", "fence")
    assert all(0 <= x <= 1 and 0 <= y <= 1 for x, y in far["polygon"])
    assert max(p[0] for p in far["polygon"]) == 1.0
    assert ns.zone_for_camera(MATRIX, ns.square((30.0, 0.0), 2.0), "z", "danger") is None


def test_catalog_row_columns():
    from eval import videos

    row = ns.catalog_row("Camera", [{"type": "running", "start_s": 1, "end_s": 3.5}], "ab")
    parsed = videos.parse_catalog(row)[0]
    assert parsed["file"] == "nvidia-w015-cam00.mp4"
    assert videos.parse_intervals(parsed["intervals"]) == [
        {"type": "running", "start_s": 1.0, "end_s": 3.5}
    ]
    assert videos.can_release(parsed["license"])
