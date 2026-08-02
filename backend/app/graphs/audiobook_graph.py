"""Narration-adaptation graph — rewrite source text to sound like an audiobook.

The Audiobook Engine reads the document's raw chunk texts and feeds them to TTS.
Raw study text is full of tables, bullet lists, page furniture, citations and
jagged line breaks — terrible to *listen* to. This graph (worker-only, off the
request path) rewrites each chunk into flowing spoken prose: same facts, same
order, no invented content, but natural audiobook narration (smooth
transitions, expanded shorthand, no tables/lists/URLs/page refs).

Mirrors notes_graph's map over RAG chunks:
  * per chunk → narration (cached by content hash so a retry never re-bills);
  * degrades permissively — if the LLM fails, the raw chunk is passed through
    so the audiobook still builds (the essence is never lost).

Off the answer path: runs inside the audiobook.build ETA job.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from app.services.chunk_map_cache import map_chunk_cached
from app.services.chunks import load_document_chunk_texts
from app.services.prompts import get_prompt
from app.services.token_budget import SUMMARIZE_CHUNK_INPUT_MAX_TOKENS

logger = logging.getLogger(__name__)

_MAP_CONCURRENCY = 4


def narration_system(db: Session) -> str:
    return get_prompt(db, "audiobook_narration_system")


async def adapt_document_for_narration(db: Session, document_id: object) -> list[str]:
    """Return per-chunk narration prose for the whole document.

    Each chunk is rewritten independently (map-only — no roll-up, so a failure
    only degrades that one chunk) and the result is cached by content hash.
    """
    chunk_texts = load_document_chunk_texts(db, document_id)
    if not chunk_texts:
        return []

    from app.services.llm_registry import default_chat_model_id

    model_id = default_chat_model_id(db)
    system = narration_system(db)
    sem = asyncio.Semaphore(_MAP_CONCURRENCY)

    async def _adapt_one(text: str) -> str:
        async with sem:
            try:
                return await map_chunk_cached(
                    db,
                    text,
                    system=system,
                    prompt_key="audiobook_narration:v1",
                    user_content=(
                        "Rewrite the section below as flowing audiobook narration. "
                        "Keep every fact and their order. No tables, lists, URLs, page "
                        "references, or citation markers. Expand shorthand naturally and "
                        "use smooth spoken transitions.\n\nSECTION:\n\n{excerpt}"
                    ),
                    log_tag="audiobook_narrate",
                    model_id=model_id,
                    max_input_tokens=SUMMARIZE_CHUNK_INPUT_MAX_TOKENS,
                )
            except Exception:
                logger.exception("audiobook narration failed for a chunk; using raw text")
                return text

    return await asyncio.gather(*[_adapt_one(t) for t in chunk_texts])
