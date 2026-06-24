"""Client IP resolution with trusted-proxy X-Forwarded-For parsing."""

from __future__ import annotations

from fastapi import Request

from app.config import get_settings


def _trusted_proxy_set() -> set[str]:
    raw = get_settings().trusted_proxy_ips
    return {ip.strip() for ip in raw.split(",") if ip.strip()}


def client_ip(request: Request) -> str:
    """Return the caller IP.

    When the direct peer is a trusted proxy, walk ``X-Forwarded-For`` from the
    right and return the rightmost hop that is not itself a trusted proxy.
    Otherwise return ``request.client.host``.
    """
    direct = request.client.host if request.client else "unknown"
    trusted = _trusted_proxy_set()
    if direct not in trusted:
        return direct

    xff = request.headers.get("x-forwarded-for", "")
    if not xff:
        return direct

    hops = [hop.strip() for hop in xff.split(",") if hop.strip()]
    for hop in reversed(hops):
        if hop not in trusted:
            return hop
    return hops[0] if hops else direct
