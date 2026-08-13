"""Presigned URLs are issued by the storage service over HTTP."""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def storage_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STORAGE_URL", "http://storage.test:8000")
    from app.config import get_settings

    get_settings.cache_clear()
    module = importlib.import_module("app.services.storage")
    return importlib.reload(module)


def test_presign_asks_storage_service(storage_module, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _Response:
        status_code = 200
        content = b""

        def json(self) -> dict:
            return {"url": "https://s3.zivo.example/zivo/demo/file.pdf?sig=1"}

    def fake_request(method: str, path: str, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["params"] = kwargs.get("params")
        return _Response()

    monkeypatch.setattr(storage_module, "_request", fake_request)
    url = storage_module.presigned_get_url("demo/file.pdf")
    assert captured["method"] == "GET"
    assert captured["path"] == "/api/storage/internal/objects/url"
    assert captured["params"] == {"key": "demo/file.pdf", "expires": 3600}
    assert url.startswith("https://s3.zivo.example/")
