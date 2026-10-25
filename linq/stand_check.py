"""Проверка на самом стенде (запускается в терминале Jupyter): что есть для работы с LinQ.

    python stand_check.py <логин> [хост брокера] [порт]
Печатает одну JSON-строку: папка программ, модули, доступность брокера. Секретов не читает.
"""

import glob
import importlib
import json
import os
import socket
import sys


def check(login: str, host: str = "10.0.114.30", port: int = 5672) -> dict:
    cache = f"/auto/cache/{login}"
    report = {
        "cache_dir": cache,
        "cache_exists": os.path.isdir(cache),
        "programs": sorted(os.path.basename(p) for p in glob.glob(cache + "/*.tpu")),
        "modules": {},
        "broker": False,
        "python": sys.version.split()[0],
    }
    for name in ("pika", "tpu_pycloud_format", "numpy", "cv2", "pytpu"):
        try:
            importlib.import_module(name)
            report["modules"][name] = True
        except Exception:
            report["modules"][name] = False
    try:
        socket.create_connection((host, port), timeout=3).close()
        report["broker"] = True
    except OSError:
        pass
    return report


if __name__ == "__main__":
    print(
        "STAND_CHECK "
        + json.dumps(check(*sys.argv[1:2], *sys.argv[2:3], *(int(a) for a in sys.argv[3:4])))
    )
