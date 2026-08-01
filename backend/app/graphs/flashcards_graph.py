"""Active-recall flashcard generation (worker-only).

Scribely-style: turn a whole source into a deck of flashcards for active recall —
straight Q/A and fill-in-the-blank (cloze) cards. Mirrors topics_graph's map-reduce
over the document's RAG chunks (no new ingest) and its tolerant JSON parsing.

Each card: ``{"front", "back", "kind"}`` where kind is "qa" or "cloze".
Off the answer path: runs in a background worker.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.orm import Session

from app.services.chunk_map_cache import map_chunk_cached
from app.services.chunks import load_document_chunk_texts
from app.services.llm_json import extract_json_array
from app.services.llm_router import complete_chat
from app.services.prompts import get_prompt
from app.services.token_budget import (
    SUMMARIZE_CHUNK_INPUT_MAX_TOKENS,
    SUMMARIZE_ROLLUP_INPUT_MAX_TOKENS,
    SUMMARIZE_SINGLE_SHOT_MAX_TOKENS,
    count_tokens,
    truncate_to_tokens,
)

_CHUNK_MAP_MAX_TOKENS = SUMMARIZE_CHUNK_INPUT_MAX_TOKENS
_ROLLUP_INPUT_MAX_TOKENS = SUMMARIZE_ROLLUP_INPUT_MAX_TOKENS
_SINGLE_SHOT_MAX_TOKENS = SUMMARIZE_SINGLE_SHOT_MAX_TOKENS
_MAP_CONCURRENCY = 6
_MAX_CARDS = 24


def _parse_cards(raw: str) -> list[dict[str, str]]:
    """Extract a JSON array of {front, back, kind} from an LLM response (tolerant)."""
    out: list[dict[str, str]] = []
    for item in extract_json_array(raw):
        front = str(item.get("front") or item.get("question") or "").strip()
        back = str(item.get("back") or item.get("answer") or "").strip()
        if not front or not back:
            continue
        kind = str(item.get("kind") or "qa").strip().lower()
        if kind not in ("qa", "cloze"):
            kind = "qa"
        out.append({"front": front[:400], "back": back[:600], "kind": kind})
    return out


def _finalize(cards: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for c in cards:
        norm = c["front"].lower().strip()
        if norm in seen:
            continue
        seen.add(norm)
        result.append(c)
        if len(result) >= _MAX_CARDS:
            break
    return result


async def generate_flashcards(db: Session, document_id: uuid.UUID) -> list[dict[str, str]]:
    """Scan the whole document's chunks and return a deck of active-recall cards."""
    chunk_texts = load_document_chunk_texts(db, document_id)
    if not chunk_texts:
        return []

    from app.services.llm_registry import default_chat_model_id

    model_id = default_chat_model_id(db)
    system = get_prompt(db, "flashcards_system")
    body = "\n\n".join(chunk_texts)

    if count_tokens(body) <= _SINGLE_SHOT_MAX_TOKENS:
        from app.services.chunk_map_cache import content_hash_key
        from app.services.generation_cache import get as cache_get, put as cache_put

        single_key = content_hash_key(
            "flashcards_generate", system, truncate_to_tokens(body, _SINGLE_SHOT_MAX_TOKENS), str(model_id)
        )
        hit = cache_get(db, kind="flashcards_generate", cache_key=single_key)
        if isinstance(hit, str) and hit.strip():
            return _finalize(_parse_cards(hit))
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"SOURCE:\n\n{truncate_to_tokens(body, _SINGLE_SHOT_MAX_TOKENS)}"},
        ]
        raw = await complete_chat(messages, db, log_tag="flashcards_generate", model_id=model_id)
        cards = _finalize(_parse_cards(raw))
        if not cards:
            # Intermittent empty/truncated completion — retry once via the failover pool.
            raw = await complete_chat(messages, db, log_tag="flashcards_generate", model_id=None)
            cards = _finalize(_parse_cards(raw))
        if cards:
            cache_put(db, kind="flashcards_generate", cache_key=single_key, value=raw)
        return cards

    sem = asyncio.Semaphore(_MAP_CONCURRENCY)

    async def _map_one(text: str) -> list[dict[str, str]]:
        async with sem:
            raw = await map_chunk_cached(
                db,
                text,
                system=system,
                prompt_key="flashcards_map:v1",
                user_content="SECTION:\n\n{excerpt}",
                log_tag="flashcards_generate",
                model_id=model_id,
                max_input_tokens=_CHUNK_MAP_MAX_TOKENS,
            )
            return _parse_cards(raw)

    partials = await asyncio.gather(*[_map_one(t) for t in chunk_texts])
    merged: list[dict[str, str]] = [c for group in partials for c in group]
    return _finalize(merged)
