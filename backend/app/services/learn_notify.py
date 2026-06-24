"""Postgres LISTEN/NOTIFY helpers for learn-queue SSE push."""

from __future__ import annotations

import uuid

from app.config import get_settings

_LEARN_NOTIFY_PREFIX = "zivo_learn_"


def learn_notify_channel(document_id: uuid.UUID) -> str:
    return f"{_LEARN_NOTIFY_PREFIX}{document_id}"


def _conninfo() -> str:
    url = get_settings().database_url
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql://", 1)
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return url


def wait_learn_notify(document_id: uuid.UUID, *, timeout: float) -> bool:
    """Block up to *timeout* seconds for a learn-progress NOTIFY (sync)."""
    import psycopg

    channel = learn_notify_channel(document_id)
    try:
        with psycopg.connect(_conninfo(), autocommit=True) as conn:
            conn.execute(f"LISTEN {channel}")
            notifies = conn.notifies(timeout=timeout)
            try:
                next(notifies)
                return True
            except StopIteration:
                return False
    except Exception:
        return False
