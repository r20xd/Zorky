"""Кадры из роликов для калибровки и для проверки квантования (нужен opencv: ml/.venv).

    python linq/frames.py <папка_с_роликами> <куда> --count 48 [--offset 0.25]

Кадры берутся равномерно по каждому ролику. У калибровки и у проверки разные смещения
(0,25 и 0,5 доли шага): кадры не совпадают, поэтому проверка не оценивает модель на том,
на чём её калибровали.
"""

from __future__ import annotations

import argparse
from pathlib import Path

VIDEO_SUFFIXES = (".mp4", ".avi", ".mkv", ".mov", ".webm")
MAX_WIDTH = 960
SKIP = ("test-loop",)  # технический ролик без людей


def plan(sizes: dict[str, int], count: int) -> dict[str, int]:
    """Сколько кадров взять с каждого ролика: поровну, остаток — самым длинным."""
    names = sorted(sizes)
    if not names or count <= 0:
        return {}
    base, extra = divmod(count, len(names))
    longest_first = sorted(names, key=lambda n: -sizes[n])
    return {n: base + (1 if n in longest_first[:extra] else 0) for n in names}


def positions(total: int, per: int, offset: float) -> list[int]:
    """Номера кадров: `per` штук, равномерно, со сдвигом offset доли шага."""
    if total <= 0 or per <= 0:
        return []
    return sorted({min(total - 1, int((k + offset) * total / per)) for k in range(per)})


def extract(folder: Path, out: Path, count: int, offset: float) -> list[Path]:
    import cv2

    out.mkdir(parents=True, exist_ok=True)
    videos = {
        p.stem: p
        for p in sorted(folder.iterdir())
        if p.suffix.lower() in VIDEO_SUFFIXES and p.stem not in SKIP
    }
    sizes = {}
    for name, path in videos.items():
        cap = cv2.VideoCapture(str(path))
        sizes[name] = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
    written: list[Path] = []
    for name, per in plan(sizes, count).items():
        cap = cv2.VideoCapture(str(videos[name]))
        for k, index in enumerate(positions(sizes[name], per, offset)):
            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = cap.read()
            if not ok:
                continue
            height, width = frame.shape[:2]
            if width > MAX_WIDTH:
                frame = cv2.resize(frame, (MAX_WIDTH, round(height * MAX_WIDTH / width)))
            target = out / f"{name}_{k:02d}.jpg"
            cv2.imwrite(str(target), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            written.append(target)
        cap.release()
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("videos")
    parser.add_argument("out")
    parser.add_argument("--count", type=int, default=48)
    parser.add_argument("--offset", type=float, default=0.25)
    args = parser.parse_args(argv)
    files = extract(Path(args.videos), Path(args.out), args.count, args.offset)
    print(f"кадров: {len(files)} -> {args.out}")
    return 0 if files else 1


if __name__ == "__main__":
    raise SystemExit(main())
