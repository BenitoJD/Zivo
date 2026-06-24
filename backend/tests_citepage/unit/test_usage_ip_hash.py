"""Client IP resolution — trusted-proxy X-Forwarded-For parsing."""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch

from app.services.request_ip import client_ip
from app.services.usage import _ip_hash


def _mock_request(
    client_host: str | None,
    xff: str | None = None,
    *,
    trusted_proxy_ips: str = "",
) -> MagicMock:
    headers: dict[str, str] = {}
    if xff is not None:
        headers["x-forwarded-for"] = xff
    request = MagicMock()
    request.client = MagicMock(host=client_host) if client_host else None
    request.headers.get = lambda name, default="": headers.get(name.lower(), default)
    return request


class TestClientIp:
    def test_returns_direct_host_when_peer_not_trusted(self) -> None:
        req = _mock_request("203.0.113.1", xff="1.1.1.1, 2.2.2.2")
        with patch("app.services.request_ip.get_settings") as mock_settings:
            mock_settings.return_value.trusted_proxy_ips = ""
            assert client_ip(req) == "203.0.113.1"

    def test_parses_xff_rightmost_untrusted_hop(self) -> None:
        req = _mock_request("10.0.0.1", xff="203.0.113.5, 10.0.0.2, 10.0.0.1")
        with patch("app.services.request_ip.get_settings") as mock_settings:
            mock_settings.return_value.trusted_proxy_ips = "10.0.0.1,10.0.0.2"
            assert client_ip(req) == "203.0.113.5"

    def test_ip_hash_uses_client_ip(self) -> None:
        req = _mock_request("203.0.113.1")
        with patch("app.services.request_ip.get_settings") as mock_settings:
            mock_settings.return_value.trusted_proxy_ips = ""
            expected = hashlib.sha256(b"203.0.113.1").hexdigest()
            assert _ip_hash(req) == expected

    def test_returns_unknown_when_no_client(self) -> None:
        req = _mock_request(None)
        with patch("app.services.request_ip.get_settings") as mock_settings:
            mock_settings.return_value.trusted_proxy_ips = ""
            assert client_ip(req) == "unknown"
