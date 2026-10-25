"""Реальное время: если инференс медленнее потока, лишние кадры пропускаются, а не копятся."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Iterator


class LatestFrames:
    """Читает источник в фоне и отдаёт только самый свежий кадр; остальные считает пропущенными.

    interval_s — темп источника для файла (иначе файл прочитался бы мгновенно);
    для потока 0: read() сам ждёт камеру.
    """

    def __init__(self, source: Iterable, interval_s: float = 0.0, sleep=time.sleep) -> None:
        self.dropped = 0
        self._source = iter(source)
        self._interval = interval_s
        self._sleep = sleep
        self._cond = threading.Condition()
        self._item: tuple | None = None
        self._done = False
        self._stop = False
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        try:
            for item in self._source:
                with self._cond:
                    if self._stop:
                        return
                    if self._item is not None:
                        self.dropped += 1  # потребитель не успел забрать прошлый кадр
                    self._item = (item,)
                    self._cond.notify_all()
                if self._interval:
                    self._sleep(self._interval)
        finally:
            with self._cond:
                self._done = True
                self._cond.notify_all()

    def __iter__(self) -> Iterator:
        while True:
            with self._cond:
                while self._item is None and not self._done:
                    self._cond.wait()
                if self._item is None:
                    return
                (item,) = self._item
                self._item = None
            yield item

    def close(self) -> None:
        with self._cond:
            self._stop = True
        close = getattr(self._source, "close", None)
        if close is not None and not self._thread.is_alive():
            close()
