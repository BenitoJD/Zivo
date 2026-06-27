"""Memory Palace generation (worker-only) — Anthony Metivier's Magnetic Memory Method.

Turns a source into a *journey*: an ordered set of "stations" through a familiar place,
each anchoring one high-yield fact with a vivid, multisensory (KAVE COGS) mnemonic image
and a recall cue. Mirrors notes_graph's map-reduce over RAG chunks (no new ingest):

  * small source → one shot (best journey coherence).
  * large source → map: pull key facts per section; reduce: build the palace from them.

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
_MIN_STATIONS = 4
_MAX_STATIONS = 8


def _slugify(term: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (term or "station").lower()).strip("-") or "station"
    base = base[:40]
    key = base
    i = 2
    while key in taken:
        key = f"{base}-{i}"
        i += 1
    taken.add(key)
    return key


def _parse_palace(raw: str) -> dict:
    """Extract the palace JSON object from an LLM response (tolerant)."""
    if not raw or not raw.strip():
        return {}
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _finalize(data: dict, fallback_setting: str) -> dict:
    raw_stations = data.get("stations")
    if not isinstance(raw_stations, list):
        return {}
    taken: set[str] = set()
    stations: list[dict[str, str]] = []
    for item in raw_stations:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term") or "").strip()
        fact = str(item.get("fact") or "").strip()
        image = str(item.get("image") or "").strip()
        if not term or not fact or not image:
            continue
        stations.append(
            {
                "key": _slugify(term, taken),
                "locus": str(item.get("locus") or "").strip()[:120],
                "term": term[:120],
                "fact": fact[:400],
                "image": image[:600],
                "cue": str(item.get("cue") or "").strip()[:200],
            }
        )
        if len(stations) >= _MAX_STATIONS:
            break
    if len(stations) < _MIN_STATIONS:
        return {}
    return {
        "setting": str(data.get("setting") or fallback_setting or "").strip()[:160],
        "intro": str(data.get("intro") or "").strip()[:400],
        "stations": stations,
    }


def _user_msg(setting: str, source: str) -> str:
    place = (setting or "").strip()
    place_line = (
        f'Use this familiar place as the setting for the journey: "{place}".\n\n'
        if place
        else "Choose one ordinary, widely-familiar place for the journey (e.g. walking "
        "through a typical home: front door → hallway → living room → kitchen → ...).\n\n"
    )
    return f"{place_line}SOURCE:\n\n{source}"


async def generate_memory_palace(
    db: Session, document_id: uuid.UUID, *, setting: str = ""
) -> dict:
    """Scan the document and return a memory-palace journey for the key facts."""
    doc = db.get(Document, document_id)
    if not doc:
        return {}
    rows = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.page_start.asc())
        .limit(200)
        .all()
    )
    chunk_texts = [r.text for r in rows if r.text]
    if not chunk_texts:
        return {}

    from app.services.llm_registry import default_chat_model_id

    model_id = default_chat_model_id(db)
    system = get_prompt(db, "memory_palace_system")
    body = "\n\n".join(chunk_texts)

    async def _build(source: str) -> dict:
        raw = await complete_chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": _user_msg(setting, source)},
            ],
            db,
            log_tag="memory_palace_generate",
            model_id=model_id,
        )
        palace = _finalize(_parse_palace(raw), setting)
        if not palace:
            # Intermittent empty/truncated completion — retry once via the failover pool.
            raw = await complete_chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": _user_msg(setting, source)},
                ],
                db,
                log_tag="memory_palace_generate",
                model_id=None,
            )
            palace = _finalize(_parse_palace(raw), setting)
        return palace

    if count_tokens(body) <= _SINGLE_SHOT_MAX_TOKENS:
        return await _build(truncate_to_tokens(body, _SINGLE_SHOT_MAX_TOKENS))

    # Large source: gather the highest-yield facts per section, then build from them.
    fact_system = get_prompt(db, "memory_facts_system")
    sem = asyncio.Semaphore(_MAP_CONCURRENCY)

    async def _facts(text: str) -> str:
        async with sem:
            excerpt = truncate_to_tokens(text, _CHUNK_MAP_MAX_TOKENS)
            return await complete_chat(
                [
                    {"role": "system", "content": fact_system},
                    {"role": "user", "content": f"SECTION:\n\n{excerpt}"},
                ],
                db,
                log_tag="memory_palace_generate",
                model_id=model_id,
            )

    partials = await asyncio.gather(*[_facts(t) for t in chunk_texts])
    facts = truncate_to_tokens("\n".join(p.strip() for p in partials if p.strip()), _ROLLUP_INPUT_MAX_TOKENS)
    if not facts.strip():
        return {}
    return await _build(facts)
