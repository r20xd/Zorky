"""Мок ML-воркера: проигрывает события из JSON в бэкенд (POST /api/events).

Время каждого события подменяется на текущее, остальное уходит как есть.
Только стандартная библиотека, подойдёт любой Python 3.10+:

    py demo/replay_events.py                 # Windows, один проход
    python3 demo/replay_events.py --loop     # Mac/Linux, по кругу до Ctrl+C
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_FILE = Path(__file__).with_name("events.sample.json")


def load_events(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: ожидается JSON-массив событий")
    return data


def with_now(event: dict, now: datetime | None = None) -> dict:
    """Копия события с текущим временем в поле ts (UTC, формат ...Z)."""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {**event, "ts": moment.isoformat().replace("+00:00", "Z")}


def post_event(backend: str, event: dict, timeout: float = 5.0) -> dict:
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("WORKER_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{backend.rstrip('/')}/api/events",
        data=json.dumps(event).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"{}")


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Мок ML-воркера: события из JSON -> бэкенд")
    parser.add_argument("--backend", default=os.environ.get("BACKEND_URL", "http://localhost:8080"))
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    parser.add_argument("--interval", type=float, default=2.0, help="пауза между событиями, с")
    parser.add_argument("--loop", action="store_true", help="по кругу до Ctrl+C")
    args = parser.parse_args(argv)

    events = load_events(args.file)
    sent = 0
    try:
        while True:
            for event in events:
                try:
                    post_event(args.backend, with_now(event))
                except urllib.error.HTTPError as err:
                    body = err.read().decode("utf-8", errors="replace")
                    print(f"бэкенд отклонил событие: {err.code} {body}", file=sys.stderr)
                    return 1
                except urllib.error.URLError as err:
                    print(f"бэкенд недоступен ({args.backend}): {err.reason}", file=sys.stderr)
                    return 1
                sent += 1
                print(f"-> {event['type']} {event['camera_id']}", flush=True)
                time.sleep(args.interval)
            if not args.loop:
                break
    except KeyboardInterrupt:
        pass
    print(f"отправлено событий: {sent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
