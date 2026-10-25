"""Добавляет только Gas_Mask из публичного PPED в общий YOLO-набор.

python3 ml/train/prepare_respirators.py --limit 10
HTTP Range позволяет скачать выбранные изображения без всего архива (3.8 ГБ).
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import io
import math
import struct
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "ppe"

SOURCE = "https://zenodo.org/records/6551758/files/PPED_data.zip?download=1"


class Cooldown:
    """429 одного потока задерживает новые запросы всех потоков."""

    def __init__(self):
        self.lock = threading.Lock()
        self.until = 0.0
        self.requests = deque()

    def defer(self, header):
        try:
            delay = float(header)
        except (TypeError, ValueError):
            try:
                delay = parsedate_to_datetime(header).timestamp() - time.time()
            except (TypeError, ValueError, OverflowError):
                delay = 60.0
        if not math.isfinite(delay) or delay < 0:
            delay = 60.0
        delay = max(60.0, delay)
        with self.lock:
            self.until = max(self.until, time.monotonic() + delay)
        print(f"HTTP 429: все потоки ждут минимум {delay:.0f} сек", flush=True)

    def wait(self):
        while True:
            with self.lock:
                now = time.monotonic()
                while self.requests and self.requests[0] <= now - 60:
                    self.requests.popleft()
                remaining = self.until - now
                if len(self.requests) >= 100:
                    remaining = max(remaining, self.requests[0] + 60 - now)
                if remaining <= 0:
                    self.requests.append(now)
                    return
            time.sleep(min(remaining, 1))


RATE_LIMIT = Cooldown()


def fetch_range(request, expected_size):
    for attempt in range(3):
        RATE_LIMIT.wait()
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status != 206:
                    raise ValueError(
                        "Источник не поддерживает HTTP Range; используйте --source-zip"
                    )
                data, headers = response.read(), response.headers
            if len(data) != expected_size:
                raise http.client.IncompleteRead(data)
            return data, headers
        except urllib.error.HTTPError as error:
            if error.code == 429:
                RATE_LIMIT.defer(error.headers.get("Retry-After"))
            elif error.code < 500:
                raise
            if attempt == 2:
                raise
        except (urllib.error.URLError, http.client.IncompleteRead, TimeoutError):
            if attempt == 2:
                raise
        print("Повтор HTTP Range после ошибки загрузки", flush=True)


class RemoteZip(io.RawIOBase):
    """Seekable HTTP-файл: сервер обязан поддерживать Range."""

    def __init__(self, url, size=None):
        self.url = url
        self.position = 0
        self.prefix = b""
        if size is not None:
            self.size = size
            return
        request = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
        _, headers = fetch_range(request, 1)
        self.size = int(headers["Content-Range"].split("/")[-1])

    def seek(self, offset, whence=0):
        self.position = offset + (self.size if whence == 2 else self.position if whence == 1 else 0)
        return self.position

    def tell(self):
        return self.position

    def seekable(self):
        return True

    def read(self, size=-1):
        end = self.size if size < 0 else min(self.position + size, self.size)
        if end <= self.position:
            return b""
        if end <= len(self.prefix):
            data = self.prefix[self.position : end]
        else:
            request = urllib.request.Request(
                self.url, headers={"Range": f"bytes={self.position}-{end - 1}"}
            )
            data, _ = fetch_range(request, end - self.position)
        self.position = end
        return data


def voc_to_yolo(xml: bytes) -> tuple[str, list[str]]:
    """VOC Gas_Mask → respirator=3; Mask и остальные классы исключаются."""
    root = ET.fromstring(xml)
    width, height = float(root.findtext("size/width")), float(root.findtext("size/height"))
    if width <= 0 or height <= 0:
        raise ValueError("Некорректный размер изображения")
    lines = []
    for obj in root.findall("object"):
        if obj.findtext("name") != "Gas_Mask":
            continue
        box = obj.find("bndbox")
        x1, y1 = max(0, float(box.findtext("xmin"))), max(0, float(box.findtext("ymin")))
        x2 = min(width, float(box.findtext("xmax")))
        y2 = min(height, float(box.findtext("ymax")))
        if x2 > x1 and y2 > y1:
            lines.append(
                f"3 {(x1 + x2) / (2 * width):.6f} {(y1 + y2) / (2 * height):.6f} "
                f"{(x2 - x1) / width:.6f} {(y2 - y1) / height:.6f}"
            )
    return Path(root.findtext("filename")).name, lines


def image_split(filename):
    # Соседние кадры одной минуты остаются в одной части, а не попадают train/val.
    group = Path(filename).stem[:13]
    return "val" if int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 5 == 0 else "train"


def remote_member(source, info, end_offset=None):
    """Читает один ZIP-member своим HTTP-указателем, проверяет размер и CRC."""
    reader = RemoteZip(source.url, size=source.size)
    reader.seek(info.header_offset)
    raw = (
        reader.read(end_offset - info.header_offset) if end_offset is not None else reader.read(30)
    )
    header = raw[:30]
    if len(header) != 30 or header[:4] != b"PK\x03\x04" or info.flag_bits & 1:
        raise ValueError("Некорректный или зашифрованный ZIP member")
    name_size, extra_size = struct.unpack("<HH", header[26:30])
    payload_offset = 30 + name_size + extra_size
    if end_offset is None:
        reader.seek(info.header_offset + payload_offset)
        compressed = reader.read(info.compress_size)
    else:
        compressed = raw[payload_offset : payload_offset + info.compress_size]
        if len(compressed) != info.compress_size:
            raise ValueError(f"Неполный ZIP member: {info.filename}")
    if info.compress_type == zipfile.ZIP_DEFLATED:
        data = zlib.decompress(compressed, -15)
    elif info.compress_type == zipfile.ZIP_STORED:
        data = compressed
    else:
        raise ValueError(f"Неизвестное сжатие ZIP: {info.compress_type}")
    if len(data) != info.file_size or zlib.crc32(data) != info.CRC:
        raise ValueError(f"Неверный размер/CRC: {info.filename}")
    return data


def save_image(target, read):
    if target.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    try:
        partial.write_bytes(read())
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def merge(archive, out, limit=None, download_workers=4):
    images = {
        PurePosixPath(n).name.casefold(): n for n in archive.namelist() if "/JPEGImages/" in n
    }
    counts = {"train": 0, "val": 0}
    selected = []
    for name in sorted(archive.namelist()):
        if not name.endswith(".xml") or "/Annotations/" not in name:
            continue
        filename, labels = voc_to_yolo(archive.read(name))
        if not labels:
            continue
        split = image_split(filename)
        if limit is not None and counts[split] >= limit:
            continue
        target_name = f"pped_{filename}"
        target = out / "images" / split / target_name
        label = out / "labels" / split / f"{Path(target_name).stem}.txt"
        selected.append((images[filename.casefold()], target, label, labels))
        counts[split] += 1
    if not all(counts.values()):
        raise ValueError(f"Нет положительных примеров в обеих частях: {counts}")

    source = archive.fp
    if isinstance(source, RemoteZip):
        # Потоки получают только ZipInfo; общий указатель archive не используется.
        offsets = sorted({info.header_offset for info in archive.infolist()} | {archive.start_dir})
        next_offset = dict(zip(offsets[:-1], offsets[1:], strict=True))
        tasks = [(archive.getinfo(name), target) for name, target, _, _ in selected]

        def download(task):
            info, target = task
            save_image(target, lambda: remote_member(source, info, next_offset[info.header_offset]))
            print(f"готово: {target.name}", flush=True)

        pool = ThreadPoolExecutor(max_workers=download_workers)
        try:
            for _ in pool.map(download, tasks):
                pass
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
    else:
        for name, target, _, _ in selected:
            save_image(target, lambda name=name: archive.read(name))
    for _, _, label, labels in selected:
        label.parent.mkdir(parents=True, exist_ok=True)
        label.write_text("\n".join(labels), encoding="utf-8")
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-zip", type=Path, help="локальный PPED_data.zip")
    parser.add_argument("--out", type=Path, default=DATA_DIR)
    parser.add_argument("--limit", type=int, help="положительных изображений на часть; иначе все")
    parser.add_argument(
        "--download-workers", type=int, default=4, help="параллельные HTTP-загрузки"
    )
    args = parser.parse_args()
    if not 1 <= args.download_workers <= 16:
        parser.error("--download-workers: от 1 до 16")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit должен быть больше нуля")
    print("Открываю PPED ZIP (HTTP Range для удалённого источника)", flush=True)
    source = args.source_zip or RemoteZip(SOURCE)
    with zipfile.ZipFile(source) as archive:
        if isinstance(source, RemoteZip):
            first_image = min(
                x.header_offset for x in archive.infolist() if "/JPEGImages/" in x.filename
            )
            print("Скачиваю VOC-аннотации", flush=True)
            source.seek(0)
            source.prefix = source.read(first_image)
        print(merge(archive, args.out, args.limit, args.download_workers))


if __name__ == "__main__":
    main()
