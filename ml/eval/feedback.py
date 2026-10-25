"""Решения оператора -> данные для дообучения (YOLO).

Берёт из бэкенда события, по которым оператор принял решение, и кладёт кадры
с разметкой в ml/data/feedback/ (вне git):
  images/<id>.jpg, labels/<id>.txt, manifest.json.
Подтверждённые (confirmed, sent_to_master, resolved): метка `класс cx cy w h`.
Ложные (false): пустой label-файл (трудный негатив), рамка и тип — в manifest.json.
Один инцидент — один кадр (последнее событие инцидента со снимком).
Класс = индекс типа в contracts/violation_types.json.

Запуск из папки ml/ (бэкенд запущен):
py -m eval.feedback [--backend http://localhost:8000] [--out data/feedback] [--login auditor]

Если на бэкенде включён вход: логин — `--login` или MOSPROM_LOGIN (роль auditor),
пароль — MOSPROM_PASSWORD или запрос getpass. Без логина — режим разработки, без входа.
"""

from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

CONFIRMED = ("confirmed", "sent_to_master", "resolved")
TYPES_PATH = Path(__file__).resolve().parents[2] / "contracts" / "violation_types.json"
PAGE = 500


def class_ids() -> dict[str, int]:
    types = json.loads(TYPES_PATH.read_text(encoding="utf-8"))["types"]
    return {t["code"]: i for i, t in enumerate(types)}


def yolo_line(cls: int, bbox: list[float]) -> str:
    x1, y1, x2, y2 = bbox
    return f"{cls} {(x1 + x2) / 2:.6f} {(y1 + y2) / 2:.6f} {x2 - x1:.6f} {y2 - y1:.6f}"


def export(
    events: list[dict],
    fetch_image: Callable[[str], bytes],
    out: Path,
    classes: dict[str, int] | None = None,
) -> list[dict]:
    """Выгружает решённые инциденты; возвращает manifest."""
    classes = classes or class_ids()
    latest: dict[str, dict] = {}
    for e in events:
        if not e.get("snapshot_url") or e["status"] not in (*CONFIRMED, "false"):
            continue
        key = e.get("incident_id") or e["id"]
        if key not in latest or e["ts"] >= latest[key]["ts"]:
            latest[key] = e
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    manifest = []
    for e in latest.values():
        name = e["id"]
        (out / "images" / f"{name}.jpg").write_bytes(fetch_image(e["snapshot_url"]))
        false = e["status"] == "false"
        line = "" if false else yolo_line(classes[e["type"]], e["bbox"]) + "\n"
        (out / "labels" / f"{name}.txt").write_text(line, encoding="utf-8")
        manifest.append(
            {
                "id": name,
                "incident_id": e.get("incident_id"),
                "type": e["type"],
                "class": classes[e["type"]],
                "bbox": e["bbox"],
                "verdict": "false" if false else "confirmed",
            }
        )
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


class AuthError(Exception):
    """Вход не удался или нужен вход (текст без пароля)."""


def make_opener(
    base: str, login: str | None = None, password: str | None = None
) -> urllib.request.OpenerDirector:
    """Opener с cookie сессии; с логином сначала входит через /api/auth/login."""
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    if login:
        if password is None:
            password = getpass.getpass(f"Пароль для {login}: ")
        req = urllib.request.Request(
            f"{base}/api/auth/login",
            data=json.dumps({"login": login, "password": password}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            opener.open(req, timeout=10).close()
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise AuthError(f"вход отклонён: неверный логин или пароль ({login})") from None
            if e.code == 429:
                raise AuthError("вход отклонён: слишком много попыток, подождите") from None
            raise
    return opener


def _open(opener: urllib.request.OpenerDirector, url: str) -> bytes:
    try:
        with opener.open(url, timeout=10) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise AuthError(
                "бэкенд требует вход (401): задайте --login / MOSPROM_LOGIN "
                "и MOSPROM_PASSWORD (роль auditor)"
            ) from None
        raise


def fetch_events(base: str, opener: urllib.request.OpenerDirector | None = None) -> list[dict]:
    opener = opener or urllib.request.build_opener()
    result: list[dict] = []
    for status in (*CONFIRMED, "false"):
        offset = 0
        while True:
            url = f"{base}/api/events?status={status}&limit={PAGE}&offset={offset}"
            page = json.loads(_open(opener, url))
            result += page
            if len(page) < PAGE:
                break
            offset += PAGE
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--backend", default="http://localhost:8000")
    p.add_argument("--out", default="data/feedback")
    p.add_argument("--login", default=os.environ.get("MOSPROM_LOGIN"))
    a = p.parse_args()
    base = a.backend.rstrip("/")
    try:
        opener = make_opener(base, a.login, os.environ.get("MOSPROM_PASSWORD"))

        def fetch_image(url: str) -> bytes:
            return _open(opener, urllib.parse.urljoin(base + "/", url))

        manifest = export(fetch_events(base, opener), fetch_image, Path(a.out))
    except AuthError as e:
        sys.exit(f"ошибка: {e}")
    print(f"выгружено кадров: {len(manifest)} -> {a.out}")


if __name__ == "__main__":
    main()
