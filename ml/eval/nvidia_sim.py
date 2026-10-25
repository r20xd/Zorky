"""Синтетический склад NVIDIA (PhysicalAI-SmartSpaces, CC BY 4.0): метки по эталонным трекам.

Сцена MTMC_Tracking_2025/val/Warehouse_015, камеры Camera, Camera_01, Camera_02 (1920x1080, 30 к/с,
5 минут). Эталон - ground_truth.json (3D-положение, размеры, поворот объекта, рамки в камерах) и
calibration.json (cameraMatrix 3x4: мировые метры -> пиксели). Метки считаются в метрах в мировой
плоскости и от нашей модели не зависят.

Запуск из папки ml/:
py -m eval.nvidia_sim build   скачать эталон (если нет), построить зоны и метки, обновить videos.md

Метки (черновик по эталону, синтетика):
- vehicle_proximity: зазор от ног до прямоугольника техники на полу < GAP_M, MIN_S и дольше;
- running: скорость человека (окно SPEED_WINDOW_S) >= RUN_MS, MIN_S и дольше;
- danger_zone: ноги в зоне danger; fence_crossing: ноги в тонкой полосе забора поперёк прохода.
Метка камеры - только пока объекты видны в этой камере (есть рамка в эталоне).
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

from eval import videos

HF = (
    "https://huggingface.co/datasets/nvidia/PhysicalAI-SmartSpaces/resolve/main/"
    "MTMC_Tracking_2025/val/Warehouse_015/"
)
SCENE = "nvidia-w015"
CAMERAS = {"Camera": "cam00", "Camera_01": "cam01", "Camera_02": "cam02"}
SRC = videos.VIDEOS / "_src" / SCENE
ZONES = Path(__file__).with_name("zones")
FPS = 30.0
FRAME = (1920, 1080)

GAP_M = 1.0  # опасное сближение: зазор до техники меньше метра
RUN_MS = 1.5  # быстрый шаг; в сцене нет настоящего бега (максимум около 1.8 м/с)
MIN_S = 1.0  # метка не короче секунды
MERGE_S = 1.0  # провалы короче секунды склеиваются
SPEED_WINDOW_S = 1.0
PERSON, VEHICLES = "Person", ("Forklift", "Transporter", "NovaCarter")
DANGER_SIDE_M = 4.0
FENCE_LEN_M, FENCE_WIDTH_M = 5.0, 0.4

AUTHOR = "NVIDIA, PhysicalAI-SmartSpaces (Omniverse), val/Warehouse_015"
LICENSE = "CC BY 4.0 (карточка набора на Hugging Face)"


# ---------- разбор эталона ----------


def parse_tracks(gt: dict) -> dict[tuple[str, int], dict[int, dict]]:
    """ground_truth.json -> {(тип, id): {кадр: {xy, size, yaw, cams: {камера: [x1,y1,x2,y2]}}}}."""
    tracks: dict[tuple[str, int], dict[int, dict]] = defaultdict(dict)
    for frame, objs in gt.items():
        for o in objs:
            loc = o["3d location"]
            tracks[(o["object type"], o["object id"])][int(frame)] = {
                "xy": (loc[0], loc[1]),
                "size": tuple(o["3d bounding box scale"][:2]),
                "yaw": o["3d bounding box rotation"][2],
                "cams": o["2d bounding box visible"],
            }
    return dict(tracks)


def rect_gap(point: tuple[float, float], center, size, yaw: float) -> float:
    """Расстояние от точки до повёрнутого прямоугольника на полу (0 внутри), метры.

    size - (ширина по локальной x, длина по локальной y), поворот yaw вокруг вертикали."""
    dx, dy = point[0] - center[0], point[1] - center[1]
    c, s = math.cos(-yaw), math.sin(-yaw)
    lx, ly = dx * c - dy * s, dx * s + dy * c
    return math.hypot(max(abs(lx) - size[0] / 2, 0.0), max(abs(ly) - size[1] / 2, 0.0))


def speed_ms(
    track: dict[int, dict], frame: int, fps: float = FPS, window_s: float = SPEED_WINDOW_S
):
    """Скорость по смещению за окно, центрированное на кадре; None - трека рядом нет."""
    half = max(1, round(window_s * fps / 2))
    a, b = track.get(frame - half), track.get(frame + half)
    if a is None or b is None:
        return None
    return math.dist(a["xy"], b["xy"]) / (2 * half / fps)


def to_intervals(
    flags: list[tuple[int, bool]], fps: float = FPS, min_s: float = MIN_S, merge_s: float = MERGE_S
) -> list[tuple[float, float]]:
    """[(кадр, да/нет)] по возрастанию кадров -> интервалы (с, по), секунды."""
    runs: list[list[int]] = []
    for frame, on in flags:
        if not on:
            continue
        if runs and (frame - runs[-1][1]) / fps <= merge_s:
            runs[-1][1] = frame
        else:
            runs.append([frame, frame])
    return [(a / fps, b / fps) for a, b in runs if (b - a) / fps >= min_s]


def point_in_polygon(p, poly) -> bool:
    x, y = p
    inside = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def person_labels(tracks, cam: str, fps: float = FPS, danger=None, fence=None) -> list[dict]:
    """Метки камеры cam; danger и fence - мировые полигоны (м) или None."""
    out: list[dict] = []
    vehicles = {k: v for k, v in tracks.items() if k[0] in VEHICLES}
    for (typ, _), tr in sorted(tracks.items()):
        if typ != PERSON:
            continue
        seen = [f for f in sorted(tr) if cam in tr[f]["cams"]]
        if not seen:
            continue
        run = [(f, (speed_ms(tr, f, fps) or 0.0) >= RUN_MS) for f in seen]
        out += _lab("running", to_intervals(run, fps))
        for zone, name in ((danger, "danger_zone"), (fence, "fence_crossing")):
            if zone:
                inside = [(f, point_in_polygon(tr[f]["xy"], zone)) for f in seen]
                out += _lab(
                    name, to_intervals(inside, fps, 0.5 if name == "fence_crossing" else MIN_S)
                )
        for vt in vehicles.values():
            near = [
                (
                    f,
                    f in vt
                    and cam in vt[f]["cams"]
                    and rect_gap(tr[f]["xy"], vt[f]["xy"], vt[f]["size"], vt[f]["yaw"]) < GAP_M,
                )
                for f in seen
            ]
            out += _lab("vehicle_proximity", to_intervals(near, fps))
    return merge_labels(out)


def _lab(typ: str, ivs: list[tuple[float, float]]) -> list[dict]:
    return [{"type": typ, "start_s": round(a, 2), "end_s": round(b, 2)} for a, b in ivs]


def merge_labels(labels: list[dict]) -> list[dict]:
    """Перекрывающиеся метки одного типа (разные люди одновременно) - в одну: событие на тип."""
    out: list[dict] = []
    for lab in sorted(labels, key=lambda x: (x["type"], x["start_s"])):
        if out and out[-1]["type"] == lab["type"] and lab["start_s"] <= out[-1]["end_s"]:
            out[-1]["end_s"] = max(out[-1]["end_s"], lab["end_s"])
        else:
            out.append(dict(lab))
    return sorted(out, key=lambda x: (x["start_s"], x["type"]))


# ---------- камеры и зоны ----------


def project(matrix, xyz) -> tuple[float, float] | None:
    """Мировая точка (м) -> пиксель по cameraMatrix 3x4; None - за камерой."""
    x, y, z = xyz
    u, v, w = (sum(matrix[r][c] * val for c, val in enumerate((x, y, z, 1.0))) for r in range(3))
    return None if w <= 1e-9 else (u / w, v / w)


def clip_unit(poly: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Отсечение выпуклого полигона по кадру 0..1 (Сазерленд - Ходжман)."""
    for axis, bound, keep_less in (
        (0, 0.0, False),
        (0, 1.0, True),
        (1, 0.0, False),
        (1, 1.0, True),
    ):
        sign = -1.0 if keep_less else 1.0

        def inside(p, axis=axis, bound=bound, sign=sign):
            return sign * (p[axis] - bound) >= 0

        res = []
        for i, cur in enumerate(poly):
            prev = poly[i - 1]
            if inside(cur) != inside(prev):
                t = (bound - prev[axis]) / (cur[axis] - prev[axis])
                res.append((prev[0] + t * (cur[0] - prev[0]), prev[1] + t * (cur[1] - prev[1])))
            if inside(cur):
                res.append(cur)
        poly = res
        if not poly:
            break
    return poly


def zone_for_camera(matrix, world_poly, zid: str, kind: str, severity: str = "high") -> dict | None:
    """Мировой полигон на полу -> зона формата API в долях кадра; None - в кадре её почти нет."""
    pts = [project(matrix, (x, y, 0.0)) for x, y in world_poly]
    if any(p is None for p in pts):
        return None
    norm = clip_unit([(u / FRAME[0], v / FRAME[1]) for u, v in pts])
    if len(norm) < 3 or _area(norm) < 0.002:
        return None
    return {
        "id": zid,
        "kind": kind,
        "polygon": [[round(x, 4), round(y, 4)] for x, y in norm],
        "severity": severity,
    }


def _area(poly) -> float:
    return (
        abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1], strict=True)))
        / 2
    )


def square(center, side: float, angle: float = 0.0) -> list[tuple[float, float]]:
    return rect(center, side, side, angle)


def rect(center, length: float, width: float, angle: float) -> list[tuple[float, float]]:
    """Прямоугольник: длина вдоль angle, ширина поперёк."""
    c, s = math.cos(angle), math.sin(angle)
    out = []
    for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        x, y = a * length / 2, b * width / 2
        out.append((center[0] + x * c - y * s, center[1] + x * s + y * c))
    return out


def pick_spots(tracks, matrices: dict[str, list], step: int = 15):
    """Две точки на полу, где люди проходят чаще всего и видно в двух камерах из трёх:
    (центр опасной зоны, центр забора, угол забора - поперёк среднего движения)."""
    cells: dict[tuple[int, int], dict] = defaultdict(lambda: {"ids": set(), "vx": 0.0, "vy": 0.0})
    for (typ, pid), tr in tracks.items():
        if typ != PERSON:
            continue
        for f in sorted(tr)[::step]:
            a, b = tr.get(f), tr.get(f + step)
            if b is None or math.dist(a["xy"], b["xy"]) / (step / FPS) < 0.5:
                continue
            cell = cells[(math.floor(a["xy"][0] / 2), math.floor(a["xy"][1] / 2))]
            cell["ids"].add(pid)
            cell["vx"] += b["xy"][0] - a["xy"][0]
            cell["vy"] += b["xy"][1] - a["xy"][1]

    def visible(c) -> bool:
        n = 0
        for m in matrices.values():
            p = project(m, (c[0], c[1], 0.0))
            n += p is not None and 150 < p[0] < FRAME[0] - 150 and 150 < p[1] < FRAME[1] - 150
        return n >= 2

    ranked = sorted(cells.items(), key=lambda kv: (-len(kv[1]["ids"]), kv[0]))
    good = [(k, v) for k, v in ranked if visible((k[0] * 2 + 1, k[1] * 2 + 1))]
    if len(good) < 2:
        raise SystemExit("не нашлось двух проходных мест, видимых в двух камерах")
    k1 = good[0][0]
    c1 = (k1[0] * 2 + 1.0, k1[1] * 2 + 1.0)
    for k2, v2 in good[1:]:
        c2 = (k2[0] * 2 + 1.0, k2[1] * 2 + 1.0)
        if math.dist(c1, c2) >= 6.0:
            # забор поперёк средней скорости: его длинная сторона перпендикулярна движению
            ang = math.atan2(v2["vy"], v2["vx"]) + math.pi / 2
            return c1, c2, ang
    raise SystemExit("второе место далеко от первого не нашлось")


# ---------- сборка ----------


def load_scene(src: Path = SRC):
    gt = json.loads((src / "ground_truth.json").read_text(encoding="utf-8"))
    cal = json.loads((src / "calibration.json").read_text(encoding="utf-8"))
    return parse_tracks(gt), {s["id"]: s["cameraMatrix"] for s in cal["sensors"]}


def fetch_sources(src: Path = SRC) -> None:
    src.mkdir(parents=True, exist_ok=True)
    for name in ("calibration.json", "ground_truth.json"):
        if not (src / name).exists():
            videos.download(HF + name, src / name)


def catalog_row(cam: str, labels: list[dict], sha: str) -> str:
    ivs = "; ".join(f"{x['type']} {x['start_s']:g}-{x['end_s']:g}" for x in labels)
    row = {
        "file": f"{SCENE}-{CAMERAS[cam]}.mp4",
        "url": f"{HF}videos/{cam}.mp4",
        "sha256": sha,
        "author": AUTHOR,
        "license": LICENSE,
        "release": "да, с указанием автора",
        "scenario": "синтетика: склад, люди и погрузчики (Omniverse); не съёмка",
        "intervals": f"{ivs} (синтетика, эталонные треки NVIDIA)" if ivs else "нет (синтетика)",
    }
    return "| " + " | ".join(row[c] for c in videos.COLUMNS) + " |"


def sync_catalog(rows: list[str], md: str) -> str:
    out, done = [], False
    for ln in md.splitlines():
        if ln.startswith(f"| {SCENE}-"):
            if not done:
                out.extend(rows)
                done = True
            continue
        out.append(ln)
    if not done:
        # после последней строки таблицы
        idx = max(i for i, ln in enumerate(out) if ln.startswith("| "))
        out[idx + 1 : idx + 1] = rows
    return "\n".join(out) + "\n"


def build() -> int:
    fetch_sources()
    tracks, matrices = load_scene()
    mats = {c: matrices[c] for c in CAMERAS}
    c_danger, c_fence, ang = pick_spots(tracks, mats)
    danger = square(c_danger, DANGER_SIDE_M)
    fence = rect(c_fence, FENCE_LEN_M, FENCE_WIDTH_M, ang)
    rows = []
    ZONES.mkdir(exist_ok=True)
    for cam, short in CAMERAS.items():
        zones = []
        for poly, zid, kind in ((danger, "dz", "danger"), (fence, "fn", "fence")):
            z = zone_for_camera(mats[cam], poly, zid, kind)
            if z:
                zones.append(z)
        # бег считается внутри зоны machinery - берём весь кадр
        zones.append(
            {
                "id": "floor",
                "kind": "machinery",
                "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
                "severity": "medium",
            }
        )
        (ZONES / f"{SCENE}-{short}.json").write_text(
            json.dumps(zones, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        have = {z["kind"] for z in zones}
        labels = person_labels(
            tracks,
            cam,
            danger=danger if "danger" in have else None,
            fence=fence if "fence" in have else None,
        )
        video = videos.VIDEOS / f"{SCENE}-{short}.mp4"
        sha = videos.sha256_file(video) if video.exists() else ""
        rows.append(catalog_row(cam, labels, sha))
        print(
            short,
            {t: sum(x["type"] == t for x in labels) for t in sorted({x["type"] for x in labels})},
        )
    videos.CATALOG.write_text(
        sync_catalog(rows, videos.CATALOG.read_text(encoding="utf-8")), encoding="utf-8"
    )
    videos.main(["labels"])
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["build"]:
        print(__doc__)
        return 2
    return build()


if __name__ == "__main__":
    sys.exit(main())
