"""Cluster-safe rate limiter backed by storage.rate_limit_hit."""

from __future__ import annotations

import logging
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.sql import text

from app.config import get_settings
from app.db import get_db
from app.services.request_ip import client_ip

logger = logging.getLogger(__name__)

_SWEEP_MIN_INTERVAL_S = 300.0
_last_sweep_monotonic: float = 0.0


def _maybe_sweep(db: Session) -> None:
    global _last_sweep_monotonic
    now = time.monotonic()
    if now - _last_sweep_monotonic < _SWEEP_MIN_INTERVAL_S:
        return
    _last_sweep_monotonic = now
    cutoff_bucket = int(time.time()) // 60 - 2
    db.execute(
        text("DELETE FROM storage.rate_limit_hit WHERE bucket < :cutoff"),
        {"cutoff": cutoff_bucket},
    )


def rate_limit(request: Request, db: Session = Depends(get_db)) -> None:
    settings = get_settings()
    limit = settings.rate_limit_per_minute
    if limit <= 0:
        return

    key = client_ip(request)
    bucket = int(time.time()) // 60

    try:
        count = db.execute(
            text(
                """
                INSERT INTO storage.rate_limit_hit (bucket, client_key, hit_count)
                VALUES (:bucket, :key, 1)
                ON CONFLICT (bucket, client_key)
                DO UPDATE SET hit_count = storage.rate_limit_hit.hit_count + 1
                RETURNING hit_count
                """
            ),
            {"bucket": bucket, "key": key},
        ).scalar()
        _maybe_sweep(db)
        db.commit()
    except SQLAlchemyError as exc:
        logger.warning("rate_limit DB failure (allowing request): %s", exc)
        db.rollback()
        return

    if count is not None and int(count) > limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


def rate_limit_dependency(_: None = Depends(rate_limit)) -> None:
    return None
