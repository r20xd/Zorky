"""Ролики для оценки: нарезка, кадры, SHA256, загрузка по videos.md, страница проверки.

Запуск из папки ml/:
py -m eval.videos cut SRC [--prefix P] [--min 10] [--max 30]  куски 10-30 с в demo/videos/
py -m eval.videos frames VIDEO [-n 4]                           кадры в demo/images/
py -m eval.videos sha VIDEO...                                  SHA256
py -m eval.videos fetch                                         скачать по videos.md, сверить SHA256
py -m eval.videos review                                        собрать eval/review.html

Ничего не публикует: ролики и кадры лежат вне git (demo/videos, demo/images).
Таблица videos.md: | файл | ссылка | sha256 | автор | лицензия | в Release | сценарий | интервалы |
(в таблице ровно 8 колонок; интервалы - «тип с-по; ...» в секундах, пометка «черновик»).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[2]
VIDEOS = ROOT / "demo" / "videos"
IMAGES = ROOT / "demo" / "images"
CATALOG = Path(__file__).with_name("videos.md")
LABELS = Path(__file__).with_name("labels.json")
REVIEW = Path(__file__).with_name("review.html")
COLUMNS = ["file", "url", "sha256", "author", "license", "release", "scenario", "intervals"]
RELEASE_OK = ("cc0", "cc by", "cc-by", "свой", "своё", "свои")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def plan_chunks(duration: float, lo: float = 10.0, hi: float = 30.0) -> list[tuple[float, float]]:
    """Границы кусков [lo, hi] с; хвост короче lo присоединяется к предыдущему куску, если влезает.

    Ролик короче lo не режется вовсе (один кусок как есть)."""
    if duration <= hi:
        return [(0.0, duration)]
    n = int(duration // hi)
    if duration - n * hi >= lo:
        n += 1
    size = duration / n  # поровну; lo <= size <= hi, так как n выбрано под это
    if size > hi:
        n += 1
        size = duration / n
    return [(round(i * size, 3), round((i + 1) * size, 3)) for i in range(n)]


def probe(path: Path) -> tuple[float, float, int, int, int]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"не открывается: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return n / fps, fps, n, w, h


def cut(src: Path, out_dir: Path, prefix: str, lo: float = 10.0, hi: float = 30.0) -> list[Path]:
    """Нарезает ролик на куски 10-30 с (mp4v, без звука). Возвращает пути кусков."""
    duration, fps, n, w, h = probe(src)
    chunks = plan_chunks(duration, lo, hi)
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(src))
    result: list[Path] = []
    pos = 0
    for i, (_, t1) in enumerate(chunks):
        end = n if i == len(chunks) - 1 else round(t1 * fps)
        name = out_dir / (f"{prefix}.mp4" if len(chunks) == 1 else f"{prefix}-{i + 1:02d}.mp4")
        wr = cv2.VideoWriter(str(name), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        while pos < end:
            ok, frame = cap.read()
            if not ok:
                break
            wr.write(frame)
            pos += 1
        wr.release()
        result.append(name)
    cap.release()
    return result


def read_frames_at(path: Path, times: list[float]) -> list[tuple[float, object]]:
    cap = cv2.VideoCapture(str(path))
    out = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if ok:
            out.append((t, frame))
    cap.release()
    return out


def frames(video: Path, out_dir: Path, count: int = 4) -> list[Path]:
    """count кадров равномерно по ролику -> JPEG demo/images/<имя>-<сек>.jpg."""
    duration = probe(video)[0]
    times = [duration * (i + 0.5) / count for i in range(count)]
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for t, frame in read_frames_at(video, times):
        p = out_dir / f"{video.stem}-{t:05.1f}s.jpg"
        cv2.imwrite(str(p), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        paths.append(p)
    return paths


def frames_in_intervals(
    video: Path, intervals: list[dict], out_dir: Path, per: int = 2
) -> list[Path]:
    """per кадров в каждом интервале нарушения -> <имя>-<тип>-<сек>.jpg."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for iv in intervals:
        a, b = iv["start_s"], iv["end_s"]
        times = [a + (b - a) * (i + 0.5) / per for i in range(per)]
        for t, frame in read_frames_at(video, times):
            p = out_dir / f"{video.stem}-{iv['type']}-{t:05.1f}s.jpg"
            cv2.imwrite(str(p), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
            paths.append(p)
    return paths


def parse_catalog(text: str) -> list[dict[str, str]]:
    """Строки таблицы videos.md с 8 колонками; шапка и разделитель пропускаются."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != len(COLUMNS) or cells[0] in ("файл", "") or set(cells[0]) <= set("-: "):
            continue
        rows.append(dict(zip(COLUMNS, (c.strip("`") for c in cells), strict=True)))
    return rows


def can_release(license_: str) -> bool:
    low = license_.lower()
    return any(k in low for k in RELEASE_OK) and "nc" not in low.split()


def parse_intervals(text: str) -> list[dict]:
    """«fall 3.0-5.5; immobile 5.5-12» -> [{type,start_s,end_s}]."""
    out = []
    for m in re.finditer(r"([a-z_]+)\s+(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", text):
        out.append({"type": m[1], "start_s": float(m[2]), "end_s": float(m[3])})
    return out


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
            while chunk := resp.read(1 << 20):
                f.write(chunk)
    except urllib.error.HTTPError as e:
        if e.code != 403 or not shutil.which("curl"):
            raise
        # Zenodo отвечает 403 клиенту urllib, curl на тот же URL получает 200
        subprocess.run(["curl", "-fsSL", "-o", str(dest), url], check=True)


def fetch_one(url: str, path: Path, cache: Path) -> None:
    """Ссылка вида https://…/архив.zip#путь/в/архиве.mp4 - архив качается один раз в cache."""
    if "#" not in url:
        download(url, path)
        return
    zurl, member = url.split("#", 1)
    cache.mkdir(parents=True, exist_ok=True)
    zpath = cache / hashlib.sha256(zurl.encode()).hexdigest()[:16]
    if not zpath.exists():
        download(zurl, zpath)
    with zipfile.ZipFile(zpath) as z, z.open(member) as src, open(path, "wb") as dst:
        while chunk := src.read(1 << 20):
            dst.write(chunk)


def fetch(rows: list[dict[str, str]], out_dir: Path = VIDEOS) -> int:
    """Качает отсутствующие файлы, сверяет SHA256. Код возврата: 0 - всё сошлось."""
    bad = 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for r in rows:
        path = out_dir / r["file"]
        if not path.exists():
            if not r["url"].startswith(("http://", "https://", "file://")):
                print(f"НЕТ ССЫЛКИ {r['file']}: {r['url']}")
                bad += 1
                continue
            fetch_one(r["url"], path, out_dir / "_src")
        got = sha256_file(path)
        if r["sha256"] and got != r["sha256"]:
            print(f"SHA256 НЕ СОШЛАСЬ {r['file']}: {got}")
            bad += 1
        else:
            print(f"ок {r['file']} {got[:12]}")
    return 1 if bad else 0


def thumb_b64(frame, width: int = 160) -> str:
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (width, max(1, round(h * width / w))))
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return base64.b64encode(buf.tobytes()).decode()


def build_review(rows: list[dict[str, str]], videos_dir: Path = VIDEOS, step: float = 0.5) -> str:
    """HTML: на ролик - источник, лицензия, интервалы-черновики и миниатюры с шагом step с."""
    parts = [
        "<!doctype html><meta charset=utf-8><title>Проверка роликов</title>",
        "<style>body{font:14px sans-serif;margin:16px}.t{display:inline-block;margin:2px;"
        "text-align:center;font-size:11px}.t img{display:block}.in{outline:3px solid #d33}"
        "h2{margin-top:28px}</style><h1>Проверка роликов</h1>",
    ]
    for r in rows:
        iv = parse_intervals(r["intervals"])
        parts.append(f"<h2>{html.escape(r['file'])}</h2>")
        parts.append(
            f"<p>сценарий: {html.escape(r['scenario'])}; автор: {html.escape(r['author'])}; "
            f"лицензия: {html.escape(r['license'])}; в Release: {html.escape(r['release'])}; "
            f"<a href='{html.escape(r['url'])}'>источник</a></p>"
            f"<p>интервалы (черновик): {html.escape(r['intervals'])}</p>"
        )
        path = videos_dir / r["file"]
        if not path.exists():
            parts.append("<p><b>файла нет, сделайте fetch</b></p>")
            continue
        duration = probe(path)[0]
        times = [i * step for i in range(int(duration / step) + 1)]
        for t, frame in read_frames_at(path, times):
            hot = any(x["start_s"] <= t <= x["end_s"] for x in iv)
            parts.append(
                f"<div class='t{' in' if hot else ''}'>"
                f"<img src='data:image/jpeg;base64,{thumb_b64(frame)}'>{t:.1f}</div>"
            )
    return "\n".join(parts)


def labels_from_catalog(rows: list[dict[str, str]]) -> list[dict]:
    """Разметка в формате eval.evaluate; camera = имя файла без расширения."""
    out = []
    for r in rows:
        for x in parse_intervals(r["intervals"]):
            lab = {"camera": Path(r["file"]).stem, **x}
            if "черновик" in r["intervals"]:
                lab["draft"] = True
            if "CSV авторов" in r["intervals"]:
                lab["source"] = "CSV авторов набора"
            out.append(lab)
    return out


PIXABAY_QUERIES = {
    "climbing": ["climbing fence", "jump over fence"],
    "machinery": ["forklift worker", "excavator worker", "truck pedestrian"],
    "phone": ["worker phone machine", "using smartphone factory"],
    "running": ["running construction site", "running warehouse"],
}
PIXABAY_UA = "Mozilla/5.0 (X11; Linux x86_64) mosprom-eval"
PIXABAY_LICENSE = "Pixabay Content License: в Release нельзя (не CC0/CC BY)"


def pixabay_get(url: str) -> dict:
    # Pixabay (Cloudflare) режет стандартный User-Agent urllib: нужен браузерный
    req = urllib.request.Request(url, headers={"User-Agent": PIXABAY_UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # URL не печатаем: в нём ключ
        try:
            body = e.read().decode("utf-8", "replace").strip()[:200]
        except Exception:
            body = ""
        raise SystemExit(f"Pixabay: HTTP {e.code}: {body}") from None
    except urllib.error.URLError as e:
        raise SystemExit(f"Pixabay: сеть недоступна: {e.reason}") from None


def pixabay_search(scenarios: list[str], per_page: int = 5, get=None) -> list[dict]:
    """Кандидаты Pixabay. Ключ только из окружения; в результат он не попадает."""
    get = get or pixabay_get
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        raise SystemExit("нет PIXABAY_API_KEY в окружении (см. .env.example)")
    out = []
    for sc in scenarios:
        for q in PIXABAY_QUERIES[sc]:
            url = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode(
                {"key": key, "q": q, "per_page": per_page, "safesearch": "true"}
            )
            for h in get(url).get("hits", []):
                out.append(
                    {
                        "scenario": sc,
                        "query": q,
                        "id": h.get("id"),
                        "page": h.get("pageURL"),
                        "author": h.get("user"),
                        "duration": h.get("duration"),
                        "tags": h.get("tags"),
                        "mp4": (h.get("videos") or {}).get("medium", {}).get("url"),
                        "license": PIXABAY_LICENSE,
                    }
                )
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cut")
    c.add_argument("src", type=Path)
    c.add_argument("--prefix")
    c.add_argument("--min", type=float, default=10.0)
    c.add_argument("--max", type=float, default=30.0)
    f = sub.add_parser("frames")
    f.add_argument("video", type=Path)
    f.add_argument("-n", type=int, default=4)
    s = sub.add_parser("sha")
    s.add_argument("files", nargs="+", type=Path)
    sub.add_parser("frames-labeled")
    sub.add_parser("fetch")
    sub.add_parser("review")
    sub.add_parser("labels")
    se = sub.add_parser("search")
    se.add_argument("--pixabay", action="store_true", required=True)
    se.add_argument("--scenario", action="append", choices=sorted(PIXABAY_QUERIES))
    a = p.parse_args(argv)

    if a.cmd == "cut":
        for q in cut(a.src, VIDEOS, a.prefix or a.src.stem, a.min, a.max):
            print(q.name, sha256_file(q))
    elif a.cmd == "frames":
        for q in frames(a.video, IMAGES, a.n):
            print(q)
    elif a.cmd == "sha":
        for q in a.files:
            print(sha256_file(q), q.name)
    elif a.cmd == "frames-labeled":
        for r in parse_catalog(CATALOG.read_text(encoding="utf-8")):
            v = VIDEOS / r["file"]
            if v.exists():
                for q in frames_in_intervals(v, parse_intervals(r["intervals"]), IMAGES):
                    print(q)
    elif a.cmd == "fetch":
        return fetch(parse_catalog(CATALOG.read_text(encoding="utf-8")))
    elif a.cmd == "review":
        REVIEW.write_text(
            build_review(parse_catalog(CATALOG.read_text(encoding="utf-8"))), encoding="utf-8"
        )
        print(REVIEW)
    elif a.cmd == "labels":
        rows = parse_catalog(CATALOG.read_text(encoding="utf-8"))
        LABELS.write_text(
            json.dumps(labels_from_catalog(rows), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    elif a.cmd == "search":
        for h in pixabay_search(a.scenario or sorted(PIXABAY_QUERIES)):
            print(json.dumps(h, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
