"""Persist LLM token usage for cost attribution."""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import pick

logger = logging.getLogger(__name__)


def record_llm_usage(
    db: Session,
    *,
    tag: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int,
    latency_ms: int,
    account_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
) -> None:
    # Independent short-lived connection, committed immediately: tokens were
    # billed even when the caller's transaction later rolls back (failed
    # generation attempts, aborted streams) — those rows must survive.
    try:
        bind = db.get_bind()
        engine = getattr(bind, "engine", bind)
    except Exception:
        logger.warning("llm_usage_event insert failed (no engine)", exc_info=True)
        return
    params = {
        "tag": tag,
        "model": model,
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "cached_tokens": int(cached_tokens),
        "latency_ms": int(latency_ms),
        "account_id": account_id,
        "document_id": document_id,
    }

    def _write() -> None:
        try:
            with engine.connect() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO qb.llm_usage_event (
                          id, tag, model, prompt_tokens, completion_tokens,
                          cached_tokens, latency_ms, account_id, document_id
                        )
                        VALUES (
                          gen_random_uuid(), :tag, :model, :prompt_tokens, :completion_tokens,
                          :cached_tokens, :latency_ms, :account_id, :document_id
                        )
                        """
                    ),
                    params,
                )
                conn.commit()
        except Exception:
            logger.warning("llm_usage_event insert failed", exc_info=True)

    # Never block the event loop on a pool checkout: in async context (chat SSE,
    # client-disconnect finalization) the write is fire-and-forget on a worker
    # thread; the engine is thread-safe. Sync callers (CPU workers) write inline.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    pick(loop is not None, lambda: loop.run_in_executor(None, _write), _write)
