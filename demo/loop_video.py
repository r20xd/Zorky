"""Зациклить видео как камеру: кадры уходят в бэкенд превью-потоком, пока процесс жив.

    py demo/loop_video.py --camera video-1 --video C:\\video\\cam.mp4 --backend http://127.0.0.1:8080

Кадры — в POST /api/cameras/<id>/frame (токен воркера из WORKER_TOKEN), по кругу, в темпе
исходного видео (не больше --fps). Нужен opencv (ml/.venv). Запускается из демо кнопкой
«Запустить» на экране «Демо-видео»; рамок и нарушений здесь нет — это только картинка камеры.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
import urllib.request


def post_frame(backend: str, camera: str, jpeg: bytes, token: str) -> bool:
    request = urllib.request.Request(
        f"{backend.rstrip('/')}/api/cameras/{camera}/frame",
        data=jpeg,
        method="POST",
        headers={
            "Content-Type": "image/jpeg",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5):
            return True
    except (urllib.error.URLError, OSError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--camera", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--backend", default=os.environ.get("BACKEND_URL", "http://127.0.0.1:8080"))
    parser.add_argument(
        "--fps", type=float, default=5.0, help="сколько кадров в секунду отправлять"
    )
    parser.add_argument("--width", type=int, default=800, help="ширина кадра, пикселей")
    parser.add_argument("--once", action="store_true", help="один проход (для проверки)")
    args = parser.parse_args(argv)

    import cv2

    token = os.environ.get("WORKER_TOKEN", "")
    capture = cv2.VideoCapture(args.video)
    if not capture.isOpened():
        print(f"не открыть видео: {args.video}", file=sys.stderr)
        return 2
    source_fps = capture.get(cv2.CAP_PROP_FPS) or args.fps
    # берём каждый N-й кадр, чтобы ролик шёл в реальном темпе, а не замедленно
    step = max(1, round(source_fps / max(1.0, args.fps)))
    interval = step / source_fps
    sent = 0
    while True:
        started = time.monotonic()
        ok, frame = capture.read()
        if not ok:  # конец ролика: сначала
            if args.once:
                break
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            continue
        for _ in range(step - 1):  # пропускаемые кадры только декодируем, без копий
            capture.grab()
        height, width = frame.shape[:2]
        if width > args.width:
            frame = cv2.resize(frame, (args.width, round(height * args.width / width)))
        done, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
        if done and post_frame(args.backend, args.camera, encoded.tobytes(), token):
            sent += 1
        time.sleep(max(0.0, interval - (time.monotonic() - started)))
    print(f"отправлено кадров: {sent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
