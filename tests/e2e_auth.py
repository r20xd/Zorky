"""e2e P8: вход, путь инцидента, задержка (бэкенд с включённым входом).

Запуск: `py tasks.py e2e`. Вручную:
  python tests/e2e_auth.py prepare <users.json>   # файл пользователей (operator, master)
  python tests/e2e_auth.py check <url> <worker-token>
Только стандартная библиотека.
"""

import hashlib
import json
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

USERS = {"op": ("operator", "op-pass-1"), "mst": ("master", "mst-pass-1")}
MAX_LATENCY_S = 2.0


def prepare(path: str) -> None:
    users = {}
    for login, (role, password) in USERS.items():
        salt = bytes(range(16))
        digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        users[login] = {"name": login, "role": role, "salt": salt.hex(), "hash": digest.hex()}
    with open(path, "w", encoding="utf-8") as file:
        json.dump(users, file)


def call(url, method="GET", body=None, headers=None, timeout=5):
    """(код, тело-json или None, заголовки); HTTP-ошибки не бросает."""
    data = None if body is None else json.dumps(body).encode()
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data, h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None), r.headers
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw), err.headers
        except ValueError:
            return err.code, None, err.headers


def login(url: str, name: str) -> dict:
    code, _, headers = call(
        f"{url}/api/auth/login", "POST",
        {"login": name, "password": USERS[name][1]},
    )  # fmt: skip
    assert code == 200, f"вход {name}: {code}"
    cookie = headers["Set-Cookie"].split(";")[0]
    return {"Cookie": cookie}


def fall(camera: str) -> dict:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "camera_id": camera, "ts": now, "frame_id": 1, "type": "fall",
        "bbox": [0.1, 0.1, 0.3, 0.9], "confidence": 0.9, "track_id": 1,
    }  # fmt: skip


def check(url: str, token: str) -> None:
    worker = {"Authorization": f"Bearer {token}"}
    cam = f"e2e-auth-{uuid.uuid4().hex[:8]}"

    # Вход: воркер без токена и с чужим токеном — 401
    assert call(f"{url}/api/events", "POST", fall(cam + "-x"))[0] == 401, "без токена не 401"
    bad = {"Authorization": "Bearer wrong"}
    assert call(f"{url}/api/events", "POST", fall(cam + "-x"), bad)[0] == 401, "чужой токен не 401"
    assert call(f"{url}/api/events")[0] == 401, "лента без входа не 401"

    op, mst = login(url, "op"), login(url, "mst")

    # operator не правит зоны
    zone = [{"id": "z", "kind": "danger", "polygon": [[0, 0], [1, 0], [1, 1]]}]
    code = call(f"{url}/api/cameras/cam-1/zones", "PUT", zone, op)[0]
    assert code == 403, f"PUT зон от operator: {code}, ожидалось 403"

    # Мастера нет в сети: падение остаётся new, ручная передача — 409
    code, event, _ = call(f"{url}/api/events", "POST", fall(cam + "-off"), worker)
    assert code == 201, f"POST события с токеном: {code}"
    time.sleep(0.3)  # фоновая передача мастеру (его нет) должна отработать
    code = call(f"{url}/api/events/{event['id']}", "PATCH", {"status": "sent_to_master"}, op)[0]
    assert code == 409, f"sent_to_master без мастера: {code}, ожидалось 409"

    # Мастер в сети: fall -> событие master по SSE
    got: dict = {}
    ready, done = threading.Event(), threading.Event()

    def listen() -> None:
        req = urllib.request.Request(f"{url}/api/stream", headers=mst)
        with urllib.request.urlopen(req, timeout=30) as response:
            name = None
            for raw in response:
                line = raw.decode().rstrip("\n")
                if line == ": connected":
                    ready.set()
                elif line.startswith("event: "):
                    name = line[7:]
                elif line.startswith("data: ") and name == "master":
                    data = json.loads(line[6:])
                    if data["camera_id"] == cam + "-on":
                        got["t"], got["event"] = time.monotonic(), data
                        done.set()
                        return

    threading.Thread(target=listen, daemon=True).start()
    assert ready.wait(10), "SSE мастера не подключился"
    t0 = time.monotonic()
    code, event, _ = call(f"{url}/api/events", "POST", fall(cam + "-on"), worker)
    assert code == 201, f"POST fall: {code}"
    assert done.wait(MAX_LATENCY_S + 3), "мастер не получил событие master"
    latency = got["t"] - t0
    print(f"задержка POST события -> событие у мастера: {latency * 1000:.0f} мс")
    assert latency < MAX_LATENCY_S, f"задержка {latency:.2f} с, нужно меньше {MAX_LATENCY_S} с"
    assert got["event"]["id"] == event["id"], "в master пришло другое событие"
    assert event["status"] == "new", f"статус в ответе POST: {event['status']}"
    assert event["status_history"] == [], f"история в ответе POST: {event['status_history']}"

    # Мастер закрывает: в истории sent_to_master, resolved; by последнего — мастер
    patch = f"{url}/api/events/{event['id']}"
    code = call(patch, "PATCH", {"status": "resolved"}, mst)[0]
    assert code == 200, f"PATCH resolved: {code}"
    code, feed, _ = call(f"{url}/api/events?limit=1000", headers=mst)
    final = next(e for e in feed if e["id"] == event["id"])
    history = final["status_history"]
    statuses = [h["status"] for h in history]
    assert statuses == ["sent_to_master", "resolved"], f"история {statuses}"
    assert history[-1]["by"] == "mst", f"by последнего: {history[-1]['by']}"


if __name__ == "__main__":
    if sys.argv[1] == "prepare":
        prepare(sys.argv[2])
    else:
        check(sys.argv[2], sys.argv[3])
        print("OK: e2e вход, путь инцидента, задержка")
