"""client_ip() — trusted-proxy X-Forwarded-For parsing, now CIDR-aware.

Regression for the prod bug where ``trusted_proxy_ips`` was empty, so the rate
limiter keyed every external request to the Traefik ingress pod IP instead of
the real client. CIDR support lets ops set the whole pod CIDR (e.g.
``10.42.0.0/16``) instead of enumerating every proxy pod IP (which changes on
redeploy).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.services import request_ip


def _req(peer: str, xff: str | None = None) -> SimpleNamespace:
    headers: dict[str, str] = {}
    if xff is not None:
        headers["x-forwarded-for"] = xff
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


def _settings(trusted: str) -> object:
    return SimpleNamespace(trusted_proxy_ips=trusted)


def test_no_trusted_proxies_returns_direct_peer() -> None:
    with patch.object(request_ip, "get_settings", return_value=_settings("")):
        assert request_ip.client_ip(_req("203.0.113.5")) == "203.0.113.5"


def test_plain_ip_trusted_proxy_walks_xff() -> None:
    with patch.object(request_ip, "get_settings", return_value=_settings("10.42.0.98")):
        req = _req("10.42.0.98", xff="203.0.113.9, 10.42.0.98")
        assert request_ip.client_ip(req) == "203.0.113.9"


def test_cidr_block_matches_pod_range() -> None:
    """The K3s case: trust the whole pod CIDR, not just one Traefik pod IP."""
    with patch.object(request_ip, "get_settings", return_value=_settings("10.42.0.0/16")):
        # Traefik at 10.42.0.98 forwards a real client IP.
        req = _req("10.42.0.98", xff="198.51.100.7")
        assert request_ip.client_ip(req) == "198.51.100.7"
        # A different Traefik pod IP in the same CIDR is also trusted.
        req2 = _req("10.42.0.201", xff="198.51.100.7")
        assert request_ip.client_ip(req2) == "198.51.100.7"


def test_multi_hop_proxy_chain_returns_first_untrusted() -> None:
    with patch.object(request_ip, "get_settings", return_value=_settings("10.42.0.0/16, 192.0.2.0/24")):
        # client -> 192.0.2.5 (trusted LB) -> 10.42.0.98 (Traefik, trusted) -> api
        req = _req("10.42.0.98", xff="203.0.113.99, 192.0.2.5, 10.42.0.98")
        assert request_ip.client_ip(req) == "203.0.113.99"


def test_xff_missing_falls_back_to_direct() -> None:
    with patch.object(request_ip, "get_settings", return_value=_settings("10.42.0.0/16")):
        # Trusted peer but no XFF header — return the peer rather than crash.
        assert request_ip.client_ip(_req("10.42.0.98")) == "10.42.0.98"


def test_invalid_config_entries_are_ignored() -> None:
    """One bad entry must not disable the whole trust set."""
    with patch.object(request_ip, "get_settings", return_value=_settings("not-an-ip, 10.42.0.0/16")):
        req = _req("10.42.0.98", xff="198.51.100.7")
        assert request_ip.client_ip(req) == "198.51.100.7"


def test_untrusted_direct_peer_ignored_even_with_xff() -> None:
    """An untrusted peer cannot spoof a client IP via XFF."""
    with patch.object(request_ip, "get_settings", return_value=_settings("10.42.0.0/16")):
        # Direct peer is a random attacker, not a trusted proxy — XFF must be ignored.
        req = _req("203.0.113.50", xff="127.0.0.1")
        assert request_ip.client_ip(req) == "203.0.113.50"
