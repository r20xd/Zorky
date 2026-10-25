"""Офлайн-прогон: видеофайл читается целиком, события пишутся в JSONL, бэкенд не нужен.

Время события = начало ролика + номер кадра / FPS (не часы компьютера), кадры не пропускаются.
Зоны ролика — eval/zones/<файл>.json (зоны как в API бэкенда); без файла зонные правила молчат.
Ту же функцию replay_frames использует eval.run по кэшу детекций: логика одна.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .dedup import OverlapDeduper
from .events import make_event
from .pipeline import find_violations
from .rules import Debouncer, Detection, Rule, build_rules

# Начало ролика по умолчанию: t = 0 с. Реального времени съёмки у роликов нет.
OFFLINE_START = datetime(2000, 1, 1, tzinfo=timezone.utc)
ZONES_DIR = Path(__file__).resolve().parents[1] / "eval" / "zones"


def frame_ts(start: datetime, frame_id: int, fps: float) -> datetime:
    return start + timedelta(seconds=frame_id / fps)


def load_zones(video: str | Path, zones_dir: Path = ZONES_DIR) -> list[dict]:
    """Зоны ролика: <имя файла>.json или <имя без расширения>.json; нет файла — нет зон."""
    path = Path(video)
    for name in (path.name + ".json", path.stem + ".json"):
        f = zones_dir / name
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    return []


def replay_frames(
    frames: Iterable[tuple[int, float, list[Detection]]],
    camera_id: str,
    rules: list[Rule],
    zones: list[dict],
    start: datetime = OFFLINE_START,
) -> Iterator[dict]:
    """(номер кадра, секунды от начала, детекции) -> события контракта; ts = start + секунды."""
    debouncer = Debouncer()
    deduper = OverlapDeduper()
    for frame_id, t, detections in frames:
        for v in find_violations(detections, zones, debouncer, t, rules):
            d = v.detection
            if not deduper.allow(v.type, d.bbox, d.track_id, t):
                continue
            yield make_event(
                camera_id=camera_id,
                frame_id=frame_id,
                violation=v.type,
                bbox=d.bbox,
                confidence=d.confidence,
                track_id=d.track_id if d.track_id >= 0 else None,
                zone_id=v.zone_id,
                severity=v.severity,
                ts=start + timedelta(seconds=t),
            )


def video_frames_all(video: str, people, ppe=None):
    """Каждый кадр файла, без пропусков: (номер, секунды, детекции). Секунды = номер / FPS."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise ValueError(f"не открыть видео: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    fps = fps if fps and fps > 0 else 25.0
    try:
        i = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            found = list(people(frame))
            if ppe is not None:
                found += ppe(frame)
            yield i, i / fps, found
            i += 1
    finally:
        cap.release()


def run_offline(
    video: str,
    camera_id: str,
    people,
    ppe,
    out: Path,
    start: datetime = OFFLINE_START,
    zones_dir: Path = ZONES_DIR,
) -> int:
    """Прогнать файл целиком, дописать события в out (JSONL). Возвращает число событий."""
    zones = load_zones(video, zones_dir)
    rules = build_rules(camera_id)
    last = [0.0]

    def counted():
        for item in video_frames_all(video, people, ppe):
            last[0] = item[1]
            yield item

    n = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        for event in replay_frames(counted(), camera_id, rules, zones, start):
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
            n += 1
    update_durations(out, camera_id, last[0])
    return n


def durations_path(out: Path) -> Path:
    return out.with_suffix(".durations.json")


def update_durations(out: Path, camera_id: str, seconds: float) -> None:
    """Длительность ролика рядом с журналом: нужна оценке для «ложных на час без нарушений»."""
    path = durations_path(out)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data[camera_id] = round(seconds, 3)
    path.write_text(json.dumps(data), encoding="utf-8")
