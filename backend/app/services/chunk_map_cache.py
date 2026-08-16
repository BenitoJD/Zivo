"""Shared cached map-step for longform study graphs.

Notes / topics / flashcards / palace / summarize each map over document chunks
with their own prompts. Caching by ``sha256(chunk)|prompt_key|model`` means a
re-run of the same tool (or worker retry) skips the LLM map entirely, while
different tools keep their own prompt-keyed entries.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.engine_runtime import pick
from app.services.generation_cache import DEFAULT_TTL_SECONDS, get as cache_get, put as cache_put
from app.services.llm_router import complete_chat
from app.services.token_budget import truncate_to_tokens


def chunk_map_key(
    chunk_text: str,
    *,
    prompt_key: str,
    model_id: uuid.UUID | None,
    user_prefix: str = "",
) -> str:
    h = hashlib.sha256()
    h.update((prompt_key or "").encode("utf-8", "ignore"))
    h.update(b"\x1f")
    h.update(str(model_id or "").encode("ascii"))
    h.update(b"\x1f")
    h.update((user_prefix or "").encode("utf-8", "ignore"))
    h.update(b"\x1f")
    h.update((chunk_text or "").encode("utf-8", "ignore"))
    return h.hexdigest()


async def map_chunk_cached(
    db: Session,
    chunk_text: str,
    *,
    system: str,
    prompt_key: str,
    user_content: str,
    log_tag: str,
    model_id: uuid.UUID | None,
    max_input_tokens: int,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    """Return LLM map output for one chunk, hitting ``generation_cache`` when possible."""
    excerpt = truncate_to_tokens(chunk_text, max_input_tokens)
    # Key on raw chunk + prompt identity (not truncated excerpt) so token-budget
    # tweaks don't silently orphan entries.
    key = chunk_map_key(
        chunk_text,
        prompt_key=prompt_key,
        model_id=model_id,
        user_prefix=user_content[:80],
    )
    hit = cache_get(db, kind="chunk_map", cache_key=key, ttl_seconds=ttl_seconds)

    async def _computed() -> str:
        raw = await complete_chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content.replace("{excerpt}", excerpt)},
            ],
            db,
            log_tag=log_tag,
            model_id=model_id,
        )
        out = (raw or "").strip()
        pick(
            bool(out),
            lambda: cache_put(db, kind="chunk_map", cache_key=key, value=out, ttl_seconds=ttl_seconds),
            lambda: None,
        )
        return out

    async def _hit() -> str:
        return hit

    return await pick(
        isinstance(hit, str) and bool(hit.strip()),
        _hit,
        _computed,
    )


def verify_verdict_key(
    *,
    stem: str,
    options: list[str],
    correct_index: int | None,
    page_text: str,
    model_id: uuid.UUID | None,
) -> str:
    h = hashlib.sha256()
    h.update((stem or "").encode("utf-8", "ignore"))
    h.update(b"\x1f")
    h.update("\x1e".join(options).encode("utf-8", "ignore"))
    h.update(b"\x1f")
    h.update(str(correct_index).encode("ascii"))
    h.update(b"\x1f")
    h.update(hashlib.sha256((page_text or "").encode("utf-8", "ignore")).hexdigest().encode("ascii"))
    h.update(b"\x1f")
    h.update(str(model_id or "").encode("ascii"))
    return h.hexdigest()


def triage_cache_key(page_text: str, page_number: int) -> str:
    digest = hashlib.sha256((page_text or "").encode("utf-8", "ignore")).hexdigest()
    return f"triage:{page_number}:{digest}"


def content_hash_key(kind: str, *parts: Any) -> str:
    h = hashlib.sha256()
    h.update(kind.encode("ascii"))
    for p in parts:
        h.update(b"\x1f")
        h.update(str(p).encode("utf-8", "ignore"))
    return h.hexdigest()
