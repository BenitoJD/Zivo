"""Client IP resolution with trusted-proxy X-Forwarded-For parsing."""

from __future__ import annotations

import ipaddress

from fastapi import Request

from app.config import get_settings


def _trusted_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    raw = get_settings().trusted_proxy_ips
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            nets.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return tuple(nets)


def _is_trusted(ip: str, trusted: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]) -> bool:
    if not trusted:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in n for n in trusted if addr.version == n.version)


def client_ip(request: Request) -> str:
    direct = request.client.host if request.client else "unknown"
    trusted = _trusted_networks()
    if not _is_trusted(direct, trusted):
        return direct

    xff = request.headers.get("x-forwarded-for", "")
    if not xff:
        return direct

    hops = [hop.strip() for hop in xff.split(",") if hop.strip()]
    for hop in reversed(hops):
        if not _is_trusted(hop, trusted):
            return hop
    return hops[0] if hops else direct
