"""Presigned URLs use the public MinIO endpoint, not the internal cluster address."""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def storage_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MINIO_ENDPOINT", "minio.zivo.svc:9000")
    monkeypatch.setenv("MINIO_SECURE", "false")
    monkeypatch.setenv("MINIO_PUBLIC_ENDPOINT", "s3.zivo.example")
    monkeypatch.setenv("MINIO_PUBLIC_SECURE", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    module = importlib.import_module("app.services.storage")
    return importlib.reload(module)


def test_presign_client_uses_public_https_endpoint(storage_module, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_client(*, endpoint: str, secure: bool):
        captured["endpoint"] = endpoint
        captured["secure"] = str(secure)

        class _Client:
            def generate_presigned_url(self, *_args, **_kwargs) -> str:
                return f"https://{endpoint}/zivo/demo/file.pdf?sig=1"

        return _Client()

    monkeypatch.setattr(storage_module, "_s3_client", fake_client)
    url = storage_module.presigned_get_url("demo/file.pdf")
    assert captured == {"endpoint": "s3.zivo.example", "secure": "True"}
    assert url.startswith("https://s3.zivo.example/")
