"""In-memory sliding-window rate limiter for expensive API routes."""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock

from fastapi import Depends, HTTPException, Request

from app.config import get_settings
from app.services.request_ip import client_ip

_WINDOW_S = 60.0
_hits: dict[str, list[float]] = defaultdict(list)
_lock = Lock()


def rate_limit(request: Request) -> None:
    settings = get_settings()
    limit = settings.rate_limit_per_minute
    if limit <= 0:
        return

    key = client_ip(request)
    now = time.monotonic()
    with _lock:
        window = _hits[key]
        window[:] = [t for t in window if now - t < _WINDOW_S]
        if len(window) >= limit:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        window.append(now)


def rate_limit_dependency(_: None = Depends(rate_limit)) -> None:
    """FastAPI dependency wrapper for ``rate_limit``."""
    return None
