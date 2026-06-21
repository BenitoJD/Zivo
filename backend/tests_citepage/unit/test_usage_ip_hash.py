"""Usage service — IP hash trust model."""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

from app.services.usage import _ip_hash


def _mock_request(client_host: str | None, xff: str | None = None) -> MagicMock:
    headers: dict[str, str] = {}
    if xff is not None:
        headers["x-forwarded-for"] = xff
    request = MagicMock()
    request.client = MagicMock(host=client_host) if client_host else None
    request.headers.get = lambda name, default="": headers.get(name.lower(), default)
    return request


class TestIpHash:
    def test_hashes_client_host(self) -> None:
        req = _mock_request("203.0.113.1")
        expected = hashlib.sha256(b"203.0.113.1").hexdigest()
        assert _ip_hash(req) == expected

    def test_ignores_xff_header(self) -> None:
        # Spoofed X-Forwarded-For must not change the hash. Trusted-proxy
        # handling belongs in the WSGI proxy (e.g. uvicorn ProxyHeadersMiddleware),
        # not in the application code, so the app must always use client.host.
        req = _mock_request("203.0.113.1", xff="1.1.1.1, 2.2.2.2")
        expected = hashlib.sha256(b"203.0.113.1").hexdigest()
        assert _ip_hash(req) == expected

    def test_returns_unknown_when_no_client(self) -> None:
        req = _mock_request(None)
        assert _ip_hash(req) == hashlib.sha256(b"unknown").hexdigest()

    def test_different_ips_produce_different_hashes(self) -> None:
        a = _ip_hash(_mock_request("203.0.113.1"))
        b = _ip_hash(_mock_request("203.0.113.2"))
        assert a != b

    def test_same_ip_produces_same_hash(self) -> None:
        a = _ip_hash(_mock_request("198.51.100.7", xff="spoofed"))
        b = _ip_hash(_mock_request("198.51.100.7"))
        assert a == b
