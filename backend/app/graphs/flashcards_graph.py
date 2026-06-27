"""Active-recall flashcard generation (worker-only).

Scribely-style: turn a whole source into a deck of flashcards for active recall —
straight Q/A and fill-in-the-blank (cloze) cards. Mirrors topics_graph's map-reduce
over the document's RAG chunks (no new ingest) and its tolerant JSON parsing.

Each card: ``{"front", "back", "kind"}`` where kind is "qa" or "cloze".
Off the answer path: runs in a background worker.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid

from sqlalchemy.orm import Session

from app.models import Document, DocumentChunk
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
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\[.*\])\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("["), text.rfind("]")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Provider truncated the array (intermittent). Salvage every complete {...} object.
        data = [
            obj
            for frag in re.findall(r"\{[^{}]*\}", text, re.DOTALL)
            if (obj := _loads_obj(frag)) is not None
        ]
    out: list[dict[str, str]] = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            front = str(item.get("front") or item.get("question") or "").strip()
            back = str(item.get("back") or item.get("answer") or "").strip()
            if not front or not back:
                continue
            kind = str(item.get("kind") or "qa").strip().lower()
            if kind not in ("qa", "cloze"):
                kind = "qa"
            out.append({"front": front[:400], "back": back[:600], "kind": kind})
    return out


def _loads_obj(fragment: str) -> dict | None:
    try:
        obj = json.loads(fragment)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


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
    doc = db.get(Document, document_id)
    if not doc:
        return []
    rows = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.page_start.asc())
        .limit(200)
        .all()
    )
    chunk_texts = [r.text for r in rows if r.text]
    if not chunk_texts:
        return []

    from app.services.llm_registry import default_chat_model_id

    model_id = default_chat_model_id(db)
    system = get_prompt(db, "flashcards_system")
    body = "\n\n".join(chunk_texts)

    if count_tokens(body) <= _SINGLE_SHOT_MAX_TOKENS:
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
        return cards

    sem = asyncio.Semaphore(_MAP_CONCURRENCY)

    async def _map_one(text: str) -> list[dict[str, str]]:
        async with sem:
            excerpt = truncate_to_tokens(text, _CHUNK_MAP_MAX_TOKENS)
            raw = await complete_chat(
                [{"role": "system", "content": system}, {"role": "user", "content": f"SECTION:\n\n{excerpt}"}],
                db,
                log_tag="flashcards_generate",
                model_id=model_id,
            )
            return _parse_cards(raw)

    partials = await asyncio.gather(*[_map_one(t) for t in chunk_texts])
    merged: list[dict[str, str]] = [c for group in partials for c in group]
    return _finalize(merged)
