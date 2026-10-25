"""Проверка трекинга на видео без бэкенда: уникальные track_id против лимита (35 на vtest.avi), FPS.

Из папки ml/:  py -m worker.trackcheck ../vtest.avi [--model yolo11n.pt] [--max-ids 35]
    [--max-frames N]
    [--conf 0.35 --imgsz 640 --track-buffer 90 --match-thresh 0.9 --track-high-thresh 0.4
     --new-track-thresh 0.5 --tracker botsort]
"""

from __future__ import annotations

import argparse
import sys
import time

from .main import TRACKER_DEFAULTS, write_tracker_yaml, yolo_detections


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка трекинга людей")
    parser.add_argument("video")
    parser.add_argument("--model", default="yolo11n.pt")
    parser.add_argument("--max-ids", type=int, default=35)
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--conf", type=float, default=0.35)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--tracker", choices=["bytetrack", "botsort"], default="bytetrack")
    for key in ("track_high_thresh", "track_low_thresh", "new_track_thresh", "match_thresh"):
        parser.add_argument(f"--{key.replace('_', '-')}", type=float, default=TRACKER_DEFAULTS[key])
    parser.add_argument("--track-buffer", type=int, default=TRACKER_DEFAULTS["track_buffer"])
    args = parser.parse_args(argv)

    import tempfile

    overrides = {
        key: getattr(args, key)
        for key in (
            "track_high_thresh",
            "track_low_thresh",
            "new_track_thresh",
            "match_thresh",
            "track_buffer",
        )
    }
    overrides["tracker_type"] = args.tracker
    if args.tracker == "botsort":
        overrides.update(
            gmc_method="sparseOptFlow", proximity_thresh=0.5, appearance_thresh=0.8, with_reid=False
        )
    tracker = write_tracker_yaml(tempfile.mktemp(suffix=".yaml"), **overrides)

    import cv2
    from ultralytics import YOLO

    model = YOLO(args.model)
    capture = cv2.VideoCapture(args.video)
    ids: set[int] = set()
    max_people = frames = 0
    started = time.monotonic()
    while args.max_frames is None or frames < args.max_frames:
        ok, frame = capture.read()
        if not ok:
            break
        detections = yolo_detections(model, frame, tracker, args.conf, args.imgsz)
        ids.update(d.track_id for d in detections)
        max_people = max(max_people, len(detections))
        frames += 1
    capture.release()
    elapsed = time.monotonic() - started
    fps = frames / elapsed if elapsed else 0.0
    print(f"параметры: {overrides} conf={args.conf} imgsz={args.imgsz}")
    print(
        f"кадров={frames} FPS={fps:.1f} уникальных_track_id={len(ids)} "
        f"макс_людей_в_кадре={max_people} лимит={args.max_ids}"
    )
    return 0 if len(ids) <= args.max_ids else 1


if __name__ == "__main__":
    sys.exit(main())
