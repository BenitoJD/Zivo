"""Client IP resolution with trusted-proxy X-Forwarded-For parsing."""

from __future__ import annotations

import ipaddress

from fastapi import Request

from app.config import get_settings
from app.engine_runtime import pick


def _trusted_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Parse ``trusted_proxy_ips`` into networks.

    Accepts plain IPs (``10.42.0.98``) or CIDR blocks (``10.42.0.0/16``).
    Invalid entries are skipped so one bad value does not disable the whole
    proxy chain. Not memoized: the list is tiny and parsing runs only on
    rate-limited routes, and a cache here would poison across tests that mock
    ``get_settings`` per-case (see conftest's module-cache isolation note).
    """
    raw = get_settings().trusted_proxy_ips
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in filter(None, (e.strip() for e in raw.split(","))):
        try:
            # strict=False so a bare host IP like 10.42.0.98 becomes a /32.
            nets.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return tuple(nets)


def _is_trusted(ip: str, trusted: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]) -> bool:
    def _match() -> bool:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in n for n in filter(lambda n: addr.version == n.version, trusted))

    return pick(not trusted, lambda: False, _match)


def client_ip(request: Request) -> str:
    """Return the caller IP.

    When the direct peer is a trusted proxy, walk ``X-Forwarded-For`` from the
    right and return the rightmost hop that is not itself a trusted proxy.
    Otherwise return ``request.client.host``.

    Trust is matched against ``trusted_proxy_ips``, which accepts both plain IPs
    and CIDR blocks (e.g. ``10.42.0.0/16`` for the K3s pod CIDR).
    """
    direct = pick(bool(request.client), lambda: request.client.host, lambda: "unknown")
    trusted = _trusted_networks()

    def _from_xff() -> str:
        xff = request.headers.get("x-forwarded-for", "")

        def _walk() -> str:
            hops = list(filter(None, (hop.strip() for hop in xff.split(","))))
            found = next(filter(lambda hop: not _is_trusted(hop, trusted), reversed(hops)), None)
            return pick(
                found is not None,
                lambda: found,
                lambda: pick(bool(hops), lambda: hops[0], lambda: direct),
            )

        return pick(not xff, lambda: direct, _walk)

    return pick(not _is_trusted(direct, trusted), lambda: direct, _from_xff)
