"""Study-notes + cheat-sheet generation (worker-only).

Scribely-style: turn a whole source into one cohesive, beautifully structured study
document in Markdown. Mirrors summarize_graph/topics_graph's map-reduce over the
document's RAG chunks (no new ingest):

  * ``kind="notes"``     → full structured notes (headers, hierarchy, key terms).
  * ``kind="cheatsheet"`` → a dense one-page revision sheet.

Off the answer path: runs in a background worker like summarize/topics.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.orm import Session

from app.models import Document
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

_CHUNK_MAP_MAX_TOKENS = SUMMARIZE_CHUNK_INPUT_MAX_TOKENS
_ROLLUP_INPUT_MAX_TOKENS = SUMMARIZE_ROLLUP_INPUT_MAX_TOKENS
_SINGLE_SHOT_MAX_TOKENS = SUMMARIZE_SINGLE_SHOT_MAX_TOKENS
_MAP_CONCURRENCY = 6

# kind → (map/single-shot prompt key, reduce/rollup prompt key)
_PROMPTS = {
    "notes": ("notes_system", "notes_rollup_system"),
    "cheatsheet": ("cheatsheet_system", "cheatsheet_system"),
}


def _clean(md: str) -> str:
    """Strip a leading ```markdown fence the model sometimes wraps the whole doc in."""
    text = (md or "").strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


async def generate_notes(db: Session, document_id: uuid.UUID, *, kind: str = "notes") -> str:
    """Scan the whole document's chunks and return one Markdown study document."""
    if kind not in _PROMPTS:
        kind = "notes"
    doc = db.get(Document, document_id)
    if not doc:
        return ""
    chunk_texts = load_document_chunk_texts(db, document_id)
    if not chunk_texts:
        return ""

    from app.services.llm_registry import default_chat_model_id

    model_id = default_chat_model_id(db)
    system_key, rollup_key = _PROMPTS[kind]
    system = get_prompt(db, system_key)
    body = "\n\n".join(chunk_texts)

    title_hint = (doc.meta or {}).get("page_title") or (doc.filename or "this material")

    if count_tokens(body) <= _SINGLE_SHOT_MAX_TOKENS:
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f'Source title: "{title_hint}"\n\n'
                    f"SOURCE:\n\n{truncate_to_tokens(body, _SINGLE_SHOT_MAX_TOKENS)}"
                ),
            },
        ]
        raw = await complete_chat(messages, db, log_tag="notes_generate", model_id=model_id)
        return _clean(raw)

    # Map: notes per section; Reduce: merge into one cohesive document.
    sem = asyncio.Semaphore(_MAP_CONCURRENCY)

    async def _map_one(text: str) -> str:
        async with sem:
            excerpt = truncate_to_tokens(text, _CHUNK_MAP_MAX_TOKENS)
            raw = await complete_chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"SECTION of \"{title_hint}\":\n\n{excerpt}"},
                ],
                db,
                log_tag="notes_generate",
                model_id=model_id,
            )
            return _clean(raw)

    partials = await asyncio.gather(*[_map_one(t) for t in chunk_texts])
    merged = "\n\n".join(p for p in partials if p)
    if not merged.strip():
        return ""

    rollup_system = get_prompt(db, rollup_key)
    listing = truncate_to_tokens(merged, _ROLLUP_INPUT_MAX_TOKENS)
    raw = await complete_chat(
        [
            {"role": "system", "content": rollup_system},
            {
                "role": "user",
                "content": (
                    f'Source title: "{title_hint}"\n\n'
                    f"Section notes to merge into one clean study document:\n\n{listing}"
                ),
            },
        ],
        db,
        log_tag="notes_rollup",
        model_id=model_id,
    )
    return _clean(raw) or _clean(merged)
