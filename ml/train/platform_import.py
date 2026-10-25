"""Загрузка исходных Ultralytics NDJSON и автоматический аудит без переразметки.

Ссылки из экспорта не выводятся в лог. Скачанные файлы не исполняются.
Это raw-аудит, не готовый общий train/val/test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import yaml

from .source_audit import image_info

SPEC = Path(__file__).with_name("platform_sources.yaml")
MAX_IMAGE_BYTES = 50 * 1024 * 1024


def normalized_box(box: list, class_count: int) -> tuple[int, list[float], bool]:
    if not isinstance(box, list) or len(box) != 5:
        raise ValueError("invalid_box_shape")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in box):
        raise ValueError("invalid_box_number")
    if not all(math.isfinite(v) for v in box):
        raise ValueError("nonfinite_box")
    c, x, y, w, h = box
    if int(c) != c or not 0 <= c < class_count:
        raise ValueError("unknown_class")
    if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1):
        raise ValueError("invalid_box_range")
    a, b, e, f = x - w / 2, y - h / 2, x + w / 2, y + h / 2
    overflow = max(0, -a, -b, e - 1, f - 1)
    if overflow > 0.0001:
        raise ValueError("box_outside_frame")
    # В экспортах округление до 5 знаков иногда выносит край на несколько 1e-5.
    a, b, e, f = max(0, a), max(0, b), min(1, e), min(1, f)
    return int(c), [(a + e) / 2, (b + f) / 2, e - a, f - b], overflow > 0


def parse_export(path: Path, spec: dict) -> tuple[dict, list[dict]]:
    header, images = None, []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("type") == "dataset" and header is None and not images:
            header = record
        elif record.get("type") == "image" and header is not None:
            images.append({**record, "line_number": line_number})
        else:
            raise ValueError(f"invalid_record_type_at_line_{line_number}")
    if not header or header.get("task") != "detect" or not images:
        raise ValueError("missing_detection_dataset_or_images")
    names = header.get("class_names", {})
    if isinstance(names, list):
        names = {str(i): name for i, name in enumerate(names)}
    expected = {str(i): name for i, name in enumerate(spec["source_classes"])}
    if names != expected:
        raise ValueError("source_classes_do_not_match_specification")
    return header, images


def annotation_record(item: dict, spec: dict) -> dict:
    mapping = spec["class_mapping"]
    result = {
        "line_number": item["line_number"],
        "file": item.get("file"),
        "source_split": item.get("split"),
        "source_size": [item.get("width"), item.get("height")],
        "target_yolo_rows": [],
        "invalid_boxes": [],
        "rounding_clipped": 0,
        "eligible_for_training": False,
    }
    annotations = item.get("annotations", {})
    if not isinstance(annotations, dict) or not isinstance(annotations.get("boxes", []), list):
        result["invalid_boxes"].append({"reason": "invalid_annotation_shape", "target": True})
        boxes = []
    else:
        boxes = annotations.get("boxes", [])
    for index, box in enumerate(boxes):
        try:
            c, xywh, clipped = normalized_box(box, len(spec["source_classes"]))
        except ValueError as error:
            c = box[0] if isinstance(box, list) and box else None
            target = not isinstance(c, (int, float)) or c not in mapping or mapping[c] is not None
            result["invalid_boxes"].append({"index": index, "reason": str(error), "target": target})
            continue
        result["rounding_clipped"] += clipped
        target = mapping[c]
        if target is not None:
            result["target_yolo_rows"].append(
                str(target) + " " + " ".join(f"{v:.8f}" for v in xywh)
            )
    if any(error["target"] for error in result["invalid_boxes"]):
        result["annotation_status"] = "quarantined_invalid_target_bbox"
    elif not result["target_yolo_rows"]:
        result["annotation_status"] = "unverified_negative_or_unlabelled"
    else:
        result["annotation_status"] = "source_target_labels_valid_partial_classes"
    return result


def download_image(url: str, target: Path) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "cdn.ul.run" or parsed.username:
        raise ValueError("unsupported_image_host")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                raw = response.read(MAX_IMAGE_BYTES + 1)
            if len(raw) > MAX_IMAGE_BYTES:
                raise ValueError("image_exceeds_size_limit")
            decoded = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
            if decoded is None:
                raise ValueError("unreadable_download")
            partial = target.with_suffix(target.suffix + ".part")
            partial.write_bytes(raw)
            partial.replace(target)
            return
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise ValueError(f"http_{error.code}") from None
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise ValueError("network_error") from None
        time.sleep(2**attempt)


def image_target(item: dict, images: Path) -> Path:
    suffix = Path(item.get("file", "")).suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        suffix = ".jpg"
    return images / f"{item['line_number']:06d}{suffix}"


def curl_batch(items: list[dict], images: Path, workers: int) -> None:
    """HTTP/2 и общий пул соединений; URL остаются только в локальном raw-каталоге."""
    config = images.parent / "download.curl"
    log = images.parent / "transfers.tsv"
    lines = []
    for item in items:
        target = image_target(item, images)
        if target.exists():
            continue
        url = item.get("url", "")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "cdn.ul.run" or parsed.username:
            raise ValueError("unsupported_image_host")
        lines += [
            "url = " + json.dumps(url),
            "output = " + json.dumps(str(target.resolve()) + ".part"),
        ]
    if not lines:
        return
    config.write_text("\n".join(lines) + "\n")
    print(f"{images.parent.name}: curl загружает {len(lines) // 2} файлов", flush=True)
    with log.open("w") as output:
        subprocess.run(
            [
                "curl",
                "--parallel",
                "--parallel-max",
                str(workers),
                "--http2",
                "--fail",
                "--silent",
                "--location",
                "--retry",
                "2",
                "--retry-delay",
                "1",
                "--max-time",
                "60",
                "--max-filesize",
                str(MAX_IMAGE_BYTES),
                "--write-out",
                "%{filename_effective}\t%{exitcode}\t%{response_code}\n",
                "--config",
                str(config),
            ],
            stdout=output,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    succeeded = 0
    for row in log.read_text().splitlines():
        fields = row.split("\t")
        if len(fields) != 3:
            continue
        name, code, status = fields
        partial = Path(name)
        if (
            code == "0"
            and status == "200"
            and partial.parent == images.resolve()
            and partial.suffix == ".part"
            and partial.is_file()
        ):
            partial.replace(partial.with_suffix(""))
            succeeded += 1
    print(f"{images.parent.name}: curl завершил {succeeded} файлов", flush=True)
    # Не сохранять дублирующий исходные URL временный config.
    config.unlink()


def import_source(
    export: Path, out: Path, spec: dict, workers: int = 4, bulk_download: bool = False
) -> dict:
    header, items = parse_export(export, spec)
    export_sha = hashlib.sha256(export.read_bytes()).hexdigest()
    out.mkdir(parents=True, exist_ok=True)
    snapshot = out / "source.ndjson"
    if snapshot.exists() and hashlib.sha256(snapshot.read_bytes()).hexdigest() != export_sha:
        raise ValueError("existing_export_snapshot_changed_use_new_output")
    if not snapshot.exists():
        shutil.copyfile(export, snapshot)
    images = out / "images"
    images.mkdir(exist_ok=True)
    if bulk_download:
        curl_batch(items, images, workers)

    def process(item):
        record = annotation_record(item, spec)
        # Имена из экспорта не используются как пути: сохраняем стабильный номер строки.
        target = image_target(item, images)
        record["local_image"] = target.relative_to(out).as_posix()
        try:
            if not target.exists():
                download_image(item.get("url", ""), target)
            info = image_info(target)
            record.update(info)
            if [info["width"], info["height"]] != record["source_size"]:
                record["image_error"] = "dimension_mismatch"
        except (ValueError, OSError, cv2.error) as error:
            # Не выводить сообщение urllib: оно может содержать исходную ссылку.
            reason = str(error) if isinstance(error, ValueError) else type(error).__name__
            record["image_error"] = reason[:100]
        return record

    records = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for number, record in enumerate(pool.map(process, items), 1):
            records.append(record)
            if number % 500 == 0:
                print(f"{export.stem}: {number}/{len(items)}", flush=True)
    summary = {
        "images": len(records),
        "downloaded_decoded": sum("sha256" in r for r in records),
        "image_errors": sum("image_error" in r for r in records),
        "source_splits": dict(Counter(r["source_split"] for r in records)),
        "annotation_status": dict(Counter(r["annotation_status"] for r in records)),
        "invalid_boxes": sum(len(r["invalid_boxes"]) for r in records),
        "rounding_clipped": sum(r["rounding_clipped"] for r in records),
    }
    report = {
        "source": spec["url"],
        "export_sha256": export_sha,
        "source_classes": header["class_names"],
        "target_mapping": spec["class_mapping"],
        "summary": summary,
        "records": records,
        "status": "raw_download_audited; deduplication and final split pending",
    }
    (out / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(export.stem, json.dumps(summary, ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exports", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--curl", action="store_true", help="общий HTTP/2 пул соединений")
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error("workers must be between 1 and 8")
    sources = yaml.safe_load(SPEC.read_text())["sources"]
    by_name = {s["url"].rsplit("/", 1)[-1]: s for s in sources}
    cv2.setNumThreads(1)
    failed = False
    for export in args.exports:
        if export.stem not in by_name:
            parser.error("unknown export filename")
        report = import_source(
            export, args.out / export.stem, by_name[export.stem], args.workers, args.curl
        )
        failed |= bool(report["summary"]["image_errors"])
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
