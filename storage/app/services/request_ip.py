"""Client IP resolution with trusted-proxy X-Forwarded-For parsing."""

from __future__ import annotations

import ipaddress

from fastapi import Request

from app.config import get_settings
from app.engine_runtime import pick


def _trusted_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    raw = get_settings().trusted_proxy_ips
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in filter(None, (e.strip() for e in raw.split(","))):
        try:
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
