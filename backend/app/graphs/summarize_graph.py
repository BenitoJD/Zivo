"""Whole-document summarize graph (worker-only, not chat)."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.orm import Session

from app.services.chunk_map_cache import map_chunk_cached
from app.services.chunks import load_document_chunk_texts
from app.services.llm_router import complete_chat
from app.services.prompts import get_prompt
from app.services.token_budget import (
    SUMMARIZE_CHUNK_INPUT_MAX_TOKENS,
    SUMMARIZE_ROLLUP_INPUT_MAX_TOKENS,
    SUMMARIZE_SINGLE_SHOT_MAX_TOKENS,
    count_tokens,
    truncate_to_tokens,
)

# Per-chunk map step + roll-up over section summaries (cacheable prefix on roll-up).
_CHUNK_MAP_MAX_TOKENS = SUMMARIZE_CHUNK_INPUT_MAX_TOKENS
_ROLLUP_INPUT_MAX_TOKENS = SUMMARIZE_ROLLUP_INPUT_MAX_TOKENS
_SINGLE_SHOT_MAX_TOKENS = SUMMARIZE_SINGLE_SHOT_MAX_TOKENS
_CHUNK_SUMMARY_CONCURRENCY = 6


async def _summarize_chunk(
    db: Session, text: str, *, system: str, model_id: uuid.UUID | None
) -> str:
    return await map_chunk_cached(
        db,
        text,
        system=system,
        prompt_key="summarize_chunk:v1",
        user_content=(
            "Summarize this excerpt in 2–4 sentences. Focus on testable facts and main ideas.\n\n"
            "{excerpt}"
        ),
        log_tag="summarize_chunk",
        model_id=model_id,
        max_input_tokens=_CHUNK_MAP_MAX_TOKENS,
    )


async def generate_whole_doc_summary(db: Session, document_id: uuid.UUID) -> str:
    chunk_texts = load_document_chunk_texts(db, document_id)
    if not chunk_texts:
        return ""

    system = get_prompt(db, "summarize_system")
    from app.services.llm_registry import default_chat_model_id

    model_id = default_chat_model_id(db)
    body = "\n\n".join(chunk_texts)

    if count_tokens(body) <= _SINGLE_SHOT_MAX_TOKENS:
        from app.services.chunk_map_cache import content_hash_key
        from app.services.generation_cache import get as cache_get, put as cache_put

        summary_key = content_hash_key(
            "summarize_doc", system, truncate_to_tokens(body, _SINGLE_SHOT_MAX_TOKENS), str(model_id)
        )
        hit = cache_get(db, kind="summarize_doc", cache_key=summary_key)
        if isinstance(hit, str) and hit.strip():
            return hit
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Summarize this document:\n\n{truncate_to_tokens(body, _SINGLE_SHOT_MAX_TOKENS)}"},
        ]
        out = (await complete_chat(messages, db, log_tag="summarize_doc", model_id=model_id)).strip()
        if out:
            cache_put(db, kind="summarize_doc", cache_key=summary_key, value=out)
        return out

    section_sem = asyncio.Semaphore(_CHUNK_SUMMARY_CONCURRENCY)

    async def _summarize_one(text: str) -> str:
        async with section_sem:
            return await _summarize_chunk(db, text, system=system, model_id=model_id)

    section_summaries = [
        s for s in await asyncio.gather(*[_summarize_one(text) for text in chunk_texts]) if s
    ]

    if not section_summaries:
        return ""

    rollup_body = truncate_to_tokens("\n\n".join(section_summaries), _ROLLUP_INPUT_MAX_TOKENS)
    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get, put as cache_put

    rollup_key = content_hash_key("summarize_rollup", system, rollup_body, str(model_id))
    hit = cache_get(db, kind="summarize_rollup", cache_key=rollup_key)
    if isinstance(hit, str) and hit.strip():
        return hit
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Section summaries:\n\n{rollup_body}"},
        {
            "role": "user",
            "content": "Write one cohesive document summary from these section summaries.",
        },
    ]
    out = (await complete_chat(messages, db, log_tag="summarize_rollup", model_id=model_id)).strip()
    if out:
        cache_put(db, kind="summarize_rollup", cache_key=rollup_key, value=out)
    return out
