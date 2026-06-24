"""Persist LLM token usage for cost attribution."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

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
    try:
        db.execute(
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
            {
                "tag": tag,
                "model": model,
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
                "cached_tokens": int(cached_tokens),
                "latency_ms": int(latency_ms),
                "account_id": account_id,
                "document_id": document_id,
            },
        )
    except Exception:
        logger.debug("llm_usage_event insert skipped (table missing or DB error)", exc_info=True)
