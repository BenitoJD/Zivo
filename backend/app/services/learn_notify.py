"""Postgres LISTEN/NOTIFY helpers for learn-queue SSE push."""

from __future__ import annotations

import logging
import uuid

from app.config import get_settings
from app.engine_runtime import Pred, Rule, apply, first_match

logger = logging.getLogger(__name__)

_LEARN_NOTIFY_PREFIX = "zivo_learn_"

_CONNINFO_RULES = (
    Rule(when=(Pred("url", "startswith", "postgresql+psycopg://"),), action="psycopg"),
    Rule(when=(Pred("url", "startswith", "postgresql+asyncpg://"),), action="asyncpg"),
    Rule(when=(), action="raw"),
)


def learn_notify_channel(document_id: uuid.UUID) -> str:
    return f"{_LEARN_NOTIFY_PREFIX}{document_id}"


def _conninfo() -> str:
    url = get_settings().database_url
    hit = first_match(_CONNINFO_RULES, {"url": url})
    return apply(
        hit.action,
        {
            "psycopg": lambda: url.replace("postgresql+psycopg://", "postgresql://", 1),
            "asyncpg": lambda: url.replace("postgresql+asyncpg://", "postgresql://", 1),
            "raw": lambda: url,
        },
    )


def wait_learn_notify(document_id: uuid.UUID, *, timeout: float) -> bool:
    """Block up to *timeout* seconds for a learn-progress NOTIFY (sync)."""
    import psycopg
    from psycopg import sql

    channel = learn_notify_channel(document_id)
    try:
        with psycopg.connect(_conninfo(), autocommit=True) as conn:
            # The channel name embeds a UUID, whose dashes are illegal in an
            # unquoted identifier — quote it so it matches pg_notify's literal
            # name exactly. (An f-string here silently failed every LISTEN,
            # leaving the SSE stream on its polling fallback.)
            conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(channel)))
            notifies = conn.notifies(timeout=timeout)
            try:
                next(notifies)
                return True
            except StopIteration:
                return False
    except Exception:
        logger.warning("learn NOTIFY listen failed; falling back to poll", exc_info=True)
        return False
