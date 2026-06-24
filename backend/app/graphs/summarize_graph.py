"""Whole-document summarize graph (worker-only, not chat)."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.orm import Session

from app.models import Document, DocumentChunk
from app.services.llm_router import complete_chat
from app.services.prompts import get_prompt
from app.services.token_budget import count_tokens, truncate_to_tokens

# Per-chunk map step + roll-up over section summaries (cacheable prefix on roll-up).
_CHUNK_MAP_MAX_TOKENS = 1_500
_ROLLUP_INPUT_MAX_TOKENS = 8_000
_SINGLE_SHOT_MAX_TOKENS = 28_000
_CHUNK_SUMMARY_CONCURRENCY = 6


async def _summarize_chunk(
    db: Session, text: str, *, system: str, model_id: uuid.UUID | None
) -> str:
    excerpt = truncate_to_tokens(text, _CHUNK_MAP_MAX_TOKENS)
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                "Summarize this excerpt in 2–4 sentences. Focus on testable facts and main ideas.\n\n"
                f"{excerpt}"
            ),
        },
    ]
    return (await complete_chat(messages, db, log_tag="summarize_chunk", model_id=model_id)).strip()


async def generate_whole_doc_summary(db: Session, document_id: uuid.UUID) -> str:
    doc = db.get(Document, document_id)
    if not doc:
        return ""
    rows = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.page_start.asc())
        .limit(200)
        .all()
    )
    chunk_texts = [r.text for r in rows if r.text]
    if not chunk_texts:
        return ""

    system = get_prompt(db, "summarize_system")
    from app.services.llm_registry import default_chat_model_id

    model_id = default_chat_model_id(db)
    body = "\n\n".join(chunk_texts)

    if count_tokens(body) <= _SINGLE_SHOT_MAX_TOKENS:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Summarize this document:\n\n{truncate_to_tokens(body, _SINGLE_SHOT_MAX_TOKENS)}"},
        ]
        return await complete_chat(messages, db, log_tag="summarize_doc", model_id=model_id)

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
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Section summaries:\n\n{rollup_body}"},
        {
            "role": "user",
            "content": "Write one cohesive document summary from these section summaries.",
        },
    ]
    return await complete_chat(messages, db, log_tag="summarize_rollup", model_id=model_id)
