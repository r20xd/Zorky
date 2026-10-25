"""Камеры из бэкенда: раз в N секунд список, запуск, остановка, перезапуск потоков."""

from __future__ import annotations

import re
import sys
import threading
import time

CAMERAS_REFRESH_S = 30.0

_USERINFO = re.compile(r"(://)[^@/?#\s]*@")
_QUERY = re.compile(r"\?[^\s]*")


def mask_source(text: object) -> str:
    """rtsp://user:pass@host/path?k=v -> rtsp://***@host/path?***. Для логов и ошибок."""
    return _QUERY.sub("?***", _USERINFO.sub(r"\1***@", str(text)))


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def stoppable(items, stop: threading.Event):
    """Кадры items, пока не взведён stop (проверка перед каждым кадром)."""
    for item in items:
        if stop.is_set():
            return
        yield item


class CameraSupervisor:
    """Держит по потоку на камеру из списка бэкенда.

    start(camera_id, source, stop) -> threading.Thread: запускает камеру; поток должен
    закончиться вскоре после stop.set().
    """

    def __init__(self, client, start, interval: float = CAMERAS_REFRESH_S) -> None:
        self.client = client
        self.start = start
        self.interval = interval
        self.running: dict[str, tuple[str, threading.Event, threading.Thread]] = {}
        self.last_list: list[dict] | None = None

    def fetch(self) -> list[dict]:
        """Свежий список; бэкенд недоступен — последний известный (пустой, пока не было)."""
        try:
            cameras = self.client.get_cameras()
        except Exception as err:  # noqa: BLE001 - любой сбой не должен ронять воркер
            _log(f"камеры: бэкенд недоступен, работаю по последнему списку: {mask_source(err)}")
            return self.last_list or []
        self.last_list = cameras
        return cameras

    def sync(self) -> None:
        cameras = self.fetch()
        wanted: dict[str, str] = {}
        for camera in cameras:
            camera_id, source = camera.get("id"), camera.get("source")
            if not camera_id:
                continue
            if not source:
                _log(f"камера {camera_id}: нет source, пропускаю")
                continue
            wanted[camera_id] = source
        for camera_id in list(self.running):
            source, stop, thread = self.running[camera_id]
            if camera_id not in wanted:
                _log(f"камера {camera_id}: удалена, останавливаю")
            elif wanted[camera_id] != source:
                _log(f"камера {camera_id}: сменила адрес на {mask_source(wanted[camera_id])}")
            elif not thread.is_alive():
                _log(f"камера {camera_id}: поток завершился, перезапускаю")
            else:
                continue
            stop.set()
            del self.running[camera_id]
        for camera_id, source in wanted.items():
            if camera_id in self.running:
                continue
            stop = threading.Event()
            try:
                thread = self.start(camera_id, source, stop)
            except Exception as err:  # noqa: BLE001
                _log(
                    f"камера {camera_id}: не запустилась ({mask_source(source)}): "
                    f"{mask_source(err)}"
                )
                continue
            self.running[camera_id] = (source, stop, thread)
            _log(f"камера {camera_id}: запущена, {mask_source(source)}")

    def run(self, sleep=time.sleep, should_stop=lambda: False) -> None:
        while not should_stop():
            self.sync()
            sleep(self.interval)

    def stop_all(self) -> None:
        for _, stop, _ in self.running.values():
            stop.set()
