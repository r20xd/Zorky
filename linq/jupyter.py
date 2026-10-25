"""Клиент Jupyter стенда по REST: вход, список и загрузка файлов, команды в терминале.

Только стандартная библиотека (для терминала нужен websocket-client). Вход — пароль (форма /login)
или токен. Ничего не печатает и не логирует секреты.
"""

from __future__ import annotations

import base64
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .config import Settings


class JupyterError(RuntimeError):
    pass


class Jupyter:
    def __init__(self, base_url: str, opener: urllib.request.OpenerDirector | None = None):
        self.base = base_url.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = opener or urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self.token = ""
        self.prefix = ""  # у JupyterHub сервер пользователя живёт под /user/<логин>

    # --- запросы ---------------------------------------------------------------------------
    def _xsrf(self, route: str = "/") -> str:
        """Токен защиты: у Hub и у сервера пользователя свои cookie _xsrf (разный path)."""
        cookies = [c for c in self.jar if c.name == "_xsrf" and route.startswith(c.path or "/")]
        cookies.sort(key=lambda c: len(c.path or "/"), reverse=True)
        return cookies[0].value if cookies else ""

    def _request(
        self, method: str, path: str, body: bytes | None = None, headers: dict | None = None
    ):
        route = self.prefix + path if path.startswith(("/api", "/terminals")) else path
        head = {"X-XSRFToken": self._xsrf(route), **(headers or {})}
        if self.token:
            head["Authorization"] = f"token {self.token}"
        request = urllib.request.Request(self.base + route, data=body, method=method, headers=head)
        try:
            return self.opener.open(request, timeout=30)
        except urllib.error.HTTPError as exc:
            raise JupyterError(f"{method} {path}: {exc.code} {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise JupyterError(
                f"{method} {path}: нет связи ({exc.reason}) — включён ли VPN?"
            ) from exc

    def _json(self, method: str, path: str, payload: dict | None = None):
        body = json.dumps(payload).encode() if payload is not None else None
        with self._request(method, path, body, {"Content-Type": "application/json"}) as response:
            raw = response.read()
        return json.loads(raw) if raw else None

    # --- вход --------------------------------------------------------------------------------
    def _text(self, method: str, path: str) -> str:
        with self._request(method, path) as response:
            return response.read().decode("utf-8", "replace")

    def _is_hub(self) -> bool:
        """JupyterHub ли это. Нет связи — ошибка, а не «не хаб» (иначе пойдём на чужой /login)."""
        try:
            return "JupyterHub" in self._text("GET", "/hub/login")
        except JupyterError as exc:
            if "нет связи" in str(exc):
                raise
            return False

    def login_hub(self, user: str, password: str, timeout: float = 180.0) -> None:
        """JupyterHub: /hub/login, запуск сервера пользователя, дальше всё под /user/<логин>."""
        page = self._text("GET", "/hub/login")
        found = re.search(r'name="_xsrf"[^>]*value="([^"]+)"', page)
        form = urllib.parse.urlencode(
            {
                "_xsrf": found.group(1) if found else self._xsrf(),
                "username": user,
                "password": password,
            }
        ).encode()
        self._request(
            "POST", "/hub/login", form, {"Content-Type": "application/x-www-form-urlencoded"}
        ).close()
        try:
            me = self._json("GET", "/hub/api/user")
        except JupyterError as exc:
            raise JupyterError("JupyterHub не принял логин или пароль") from exc
        if not me or me.get("name") != user:
            raise JupyterError("JupyterHub не принял логин или пароль")
        self._ensure_server(user, timeout)
        self.prefix = f"/user/{urllib.parse.quote(user)}"
        self._json("GET", "/api/status")  # cookie _xsrf самого сервера пользователя

    def _ensure_server(self, user: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        started = False
        while time.monotonic() < deadline:
            info = self._json("GET", f"/hub/api/users/{urllib.parse.quote(user)}") or {}
            server = (info.get("servers") or {}).get("")
            if server and server.get("ready"):
                return
            if not server and not info.get("pending") and not started:
                self._request(
                    "POST", f"/hub/api/users/{urllib.parse.quote(user)}/server", b""
                ).close()
                started = True
            time.sleep(2)
        raise JupyterError("сервер Jupyter не запустился за отведённое время")

    def login(self, password: str = "", token: str = "", user: str = "") -> None:
        """Токен — в заголовок; пароль — форма JupyterHub (/hub/login) или Jupyter (/login)."""
        if not token and user and password and self._is_hub():
            self.login_hub(user, password)
            return
        if token:
            self.token = token
            self._json("GET", "/api/status")
            return
        if not password:
            raise JupyterError("нужен JUPYTER_PASS или JUPYTER_TOKEN")
        self._request("GET", "/login").close()
        form = urllib.parse.urlencode({"_xsrf": self._xsrf(), "password": password}).encode()
        self._request(
            "POST", "/login", form, {"Content-Type": "application/x-www-form-urlencoded"}
        ).close()
        try:
            self._json("GET", "/api/status")
        except JupyterError as exc:
            raise JupyterError("Jupyter не принял пароль (или API закрыт)") from exc

    # --- файлы -------------------------------------------------------------------------------
    @staticmethod
    def _quote(path: str) -> str:
        return urllib.parse.quote(path.strip("/"), safe="/")

    def listdir(self, path: str) -> list[str]:
        data = self._json("GET", f"/api/contents/{self._quote(path)}")
        return sorted(item["name"] for item in data.get("content", []))

    def mkdir(self, path: str) -> None:
        """Папка в домашнем каталоге пользователя (если уже есть - ничего не делает)."""
        parts = path.strip("/").split("/")
        for i in range(1, len(parts) + 1):
            current = "/".join(parts[:i])
            try:
                self._json("PUT", f"/api/contents/{self._quote(current)}", {"type": "directory"})
            except JupyterError:
                pass  # уже существует

    def upload(self, remote: str, data: bytes) -> None:
        payload = {"type": "file", "format": "base64", "content": base64.b64encode(data).decode()}
        self._json("PUT", f"/api/contents/{self._quote(remote)}", payload)

    def upload_file(self, local: Path, remote_dir: str) -> str:
        self.mkdir(remote_dir)
        remote = f"{remote_dir.strip('/')}/{local.name}"
        self.upload(remote, local.read_bytes())
        return remote

    def download(self, remote: str) -> bytes:
        data = self._json(
            "GET", f"/api/contents/{self._quote(remote)}?content=1&type=file&format=base64"
        )
        return base64.b64decode(data["content"])

    # --- терминал ----------------------------------------------------------------------------
    def run(self, command: str, timeout: float = 120.0, connect=None) -> tuple[int, str]:
        """Команда в новом терминале стенда: (код возврата, вывод). Нужен пакет websocket-client."""
        term = self._json("POST", "/api/terminals", {})["name"]
        try:
            return self._run_in(term, command, timeout, connect)
        finally:
            try:
                self._request("DELETE", f"/api/terminals/{term}").close()
            except JupyterError:
                pass

    def _run_in(self, term: str, command: str, timeout: float, connect) -> tuple[int, str]:
        if connect is None:
            try:
                import websocket
            except ImportError as exc:  # pragma: no cover
                raise JupyterError("для терминала: pip install websocket-client") from exc
            connect = websocket.create_connection
        scheme = "wss" if self.base.startswith("https") else "ws"
        host = self.base.split("://", 1)[1]
        url = f"{scheme}://{host}{self.prefix}/terminals/websocket/{term}"
        cookie = "; ".join(f"{c.name}={c.value}" for c in self.jar)
        headers = [f"Cookie: {cookie}"] if cookie else []
        if self.token:
            headers.append(f"Authorization: token {self.token}")
        mark = uuid.uuid4().hex[:8]
        ws = connect(url, header=headers, timeout=10)
        try:
            ws.send(json.dumps(["stdin", f"{command}\necho __END_{mark}_$?__\n"]))
            out, deadline = "", time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    kind, *rest = json.loads(ws.recv())
                except Exception:
                    continue
                if kind == "stdout":
                    out += rest[0]
                    # эхо самой команды содержит «$?», настоящий конец - с числом кода
                    done = re.search(rf"__END_{mark}_(\d+)__", out)
                    if done:
                        return int(done.group(1)), out[: done.start()]
            raise JupyterError(f"команда не завершилась за {timeout:.0f} с")
        finally:
            ws.close()


def connect(settings: Settings) -> Jupyter:
    """Jupyter со входом по данным из настроек."""
    client = Jupyter(settings.jupyter_url)
    client.login(
        password=settings.jupyter_pass, token=settings.jupyter_token, user=settings.jupyter_login
    )
    return client
