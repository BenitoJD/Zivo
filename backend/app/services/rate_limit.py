"""Cluster-safe rate limiter for expensive API routes.

Backed by ``qb.rate_limit_hit`` (Alembic 033) so the limit holds across all API
pods — previously the in-memory counter was per-process, so with N replicas the
effective limit was ``rate_limit_per_minute * N``.

Design:
- Fixed-minute bucket per client IP: ``bucket = floor(unix_epoch / 60)``.
  Every hit is one atomic ``INSERT ... ON CONFLICT DO UPDATE ... RETURNING``
  (the same cluster-safe pattern as ``demo_usage`` in services/usage.py).
- Old buckets age out via a throttled sweep (~once per 5 min per pod) so the
  table stays tiny.
- DB failures fail OPEN (log + allow). Rate limiting is an availability
  safeguard, not a security gate; we never 500 a user over bookkeeping.
  Auth/CSRF/ownership guards remain authoritative for authorization.
- ``rate_limit_per_minute <= 0`` disables the limiter entirely.
"""

from __future__ import annotations

import logging
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.sql import text

from app.config import get_settings
from app.db import get_db
from app.engine_runtime import pick
from app.services.request_ip import client_ip

logger = logging.getLogger(__name__)

_SWEEP_MIN_INTERVAL_S = 300.0  # sweep at most once per 5 min per pod
_last_sweep_monotonic: float = 0.0


def _maybe_sweep(db: Session) -> None:
    """Throttled delete of aged buckets. Called on every hit but only runs
    ~once per ``_SWEEP_MIN_INTERVAL_S``; the global timestamp makes it cheap
    and self-healing across pod restarts."""
    global _last_sweep_monotonic
    now = time.monotonic()

    def _sweep() -> None:
        global _last_sweep_monotonic
        _last_sweep_monotonic = now
        cutoff_bucket = int(time.time()) // 60 - 2
        db.execute(
            text("DELETE FROM qb.rate_limit_hit WHERE bucket < :cutoff"),
            {"cutoff": cutoff_bucket},
        )

    pick(now - _last_sweep_monotonic < _SWEEP_MIN_INTERVAL_S, lambda: None, _sweep)


def rate_limit(request: Request, db: Session = Depends(get_db)) -> None:
    """Increment the per-client, per-minute counter and 429 when over budget.

    Fails open on DB errors: a transient outage must not turn the limiter into
    a site-wide outage.
    """
    settings = get_settings()
    limit = settings.rate_limit_per_minute

    def _check() -> None:
        key = client_ip(request)
        bucket = int(time.time()) // 60

        try:
            count = db.execute(
                text(
                    """
                    INSERT INTO qb.rate_limit_hit (bucket, client_key, hit_count)
                    VALUES (:bucket, :key, 1)
                    ON CONFLICT (bucket, client_key)
                    DO UPDATE SET hit_count = qb.rate_limit_hit.hit_count + 1
                    RETURNING hit_count
                    """
                ),
                {"bucket": bucket, "key": key},
            ).scalar()
            _maybe_sweep(db)
            db.commit()
        except SQLAlchemyError as exc:
            # Fail open — never block a user over limiter bookkeeping.
            logger.warning("rate_limit DB failure (allowing request): %s", exc)
            db.rollback()
            return

        def _reject() -> None:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

        pick(count is not None and int(count) > limit, _reject, lambda: None)

    pick(limit <= 0, lambda: None, _check)


def rate_limit_dependency(_: None = Depends(rate_limit)) -> None:
    """FastAPI dependency wrapper for ``rate_limit``."""
    return None
