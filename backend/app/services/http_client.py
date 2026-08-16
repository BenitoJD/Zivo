"""Shared HTTP client constants + factory for outbound calls to public APIs.

Single source of truth for the project's User-Agent string and default timeout,
so every outbound fetch identifies the product consistently (per the public-API
etiquette the User-Agent describes) and shares the same generous default budget.
"""

from __future__ import annotations

import httpx

from app.engine_runtime import choose

# Used by every outbound call to a public API. Identifies the product + points
# back at the public site per the convention public-API providers expect.
USER_AGENT = "zivo/1.0 (+https://zivo.fyi)"

# Generous default — public APIs (Wikidata, Wikipedia, article fetches) can be slow.
HTTP_TIMEOUT_S = 15.0


def zivo_http_client(
    *,
    timeout: float = HTTP_TIMEOUT_S,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = True,
) -> httpx.AsyncClient:
    """Build an httpx AsyncClient with the canonical Zivo User-Agent pre-applied.

    Caller-supplied `headers` are merged on top (and win on conflict), so a route
    that needs extra headers (e.g. an Accept for content negotiation) can still add
    them without re-specifying the User-Agent.
    """
    merged = {"User-Agent": USER_AGENT, **choose(bool(headers), headers or {}, {})}
    return httpx.AsyncClient(timeout=timeout, headers=merged, follow_redirects=follow_redirects)
