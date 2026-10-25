"""Клиент бэкенда на стандартной библиотеке. Воркер только отправляет данные и забирает зоны."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)


class BackendAuthError(urllib.error.URLError):
    """Бэкенд ответил 401. Значение токена в сообщение не попадает."""


class BackendClient:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._token = os.environ.get("WORKER_TOKEN", "").strip()
        if not self._token:
            log.warning("WORKER_TOKEN не задан: запросы к бэкенду идут без Authorization")

    def _request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        content_type: str = "application/json",
    ):
        headers = {"Content-Type": content_type} if body is not None else {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as err:
            if err.code == 401:
                raise BackendAuthError("неверный или пустой WORKER_TOKEN") from None
            raise
        return json.loads(raw) if raw else None

    @staticmethod
    def _camera(camera_id: str) -> str:
        return urllib.parse.quote(camera_id, safe="")

    def post_event(self, event: dict) -> dict:
        return self._request("POST", "/api/events", json.dumps(event).encode("utf-8"))

    def get_cameras(self) -> list[dict]:
        """Камеры с source (воркеру по токену отдаётся и source)."""
        return self._request("GET", "/api/cameras") or []

    def get_zones(self, camera_id: str) -> list[dict]:
        return self._request("GET", f"/api/cameras/{self._camera(camera_id)}/zones") or []

    def get_rules(self, camera_id: str) -> dict:
        """{"enabled": [коды] или None, "source": camera|area|default} — что контролировать."""
        return self._request("GET", f"/api/cameras/{self._camera(camera_id)}/rules") or {}

    def post_frame(self, camera_id: str, jpeg: bytes) -> None:
        """Превью-кадр для живого видео на дашборде — заработает после пункта B4."""
        self._request("POST", f"/api/cameras/{self._camera(camera_id)}/frame", jpeg, "image/jpeg")

    def post_snapshot(self, jpeg: bytes) -> str:
        """Снимок нарушения; возвращает url для snapshot_url события."""
        return self._request("POST", "/api/snapshots", jpeg, "image/jpeg")["url"]

    def post_metrics(self, metrics: dict) -> None:
        self._request("POST", "/api/metrics", json.dumps(metrics).encode("utf-8"))
