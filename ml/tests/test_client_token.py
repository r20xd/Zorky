import io
import logging
import urllib.error

import pytest

from worker import client as client_mod
from worker.client import BackendAuthError, BackendClient


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def capture(monkeypatch, response=b"{}"):
    seen = []

    def fake(request, timeout=None):
        seen.append(request)
        return Resp(response)

    monkeypatch.setattr(client_mod.urllib.request, "urlopen", fake)
    return seen


def test_header_on_all_requests(monkeypatch):
    monkeypatch.setenv("WORKER_TOKEN", "s3cret")
    seen = capture(monkeypatch, b'{"url": "/s/1"}')
    c = BackendClient("http://x")
    c.post_event({"a": 1})
    c.get_zones("cam 1")
    c.post_frame("cam-1", b"j")
    c.post_snapshot(b"j")
    c.post_metrics({"fps": 1})
    assert len(seen) == 5
    assert all(r.get_header("Authorization") == "Bearer s3cret" for r in seen)


def test_no_token_no_header_and_one_warning(monkeypatch, caplog):
    monkeypatch.delenv("WORKER_TOKEN", raising=False)
    seen = capture(monkeypatch)
    with caplog.at_level(logging.WARNING):
        c = BackendClient("http://x")
        c.post_event({})
        c.post_metrics({})
    assert all(r.get_header("Authorization") is None for r in seen)
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert "s3cret" not in caplog.text


def test_401_error_without_token(monkeypatch):
    monkeypatch.setenv("WORKER_TOKEN", "s3cret")

    def fake(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(client_mod.urllib.request, "urlopen", fake)
    with pytest.raises(BackendAuthError) as info:
        BackendClient("http://x").post_event({})
    assert "неверный или пустой WORKER_TOKEN" in str(info.value)
    assert "s3cret" not in str(info.value)
    assert "s3cret" not in repr(info.value)
