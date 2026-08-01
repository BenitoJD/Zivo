"""Topic-outline extraction for the Explain feature (worker-only).

Mirrors summarize_graph's map-reduce over document chunks, but instead of one prose
summary it produces a structured outline of the topics a learner should understand,
in reading order. Outputs a list of ``{"key", "title", "summary"}`` dicts.

Reuses the existing RAG chunks (``document_chunks``) — no new ingest. Off the answer
path: runs in a background worker like summarize/generation.
"""

from __future__ import annotations

import asyncio
import re
import uuid

from sqlalchemy.orm import Session

from app.models import DocumentChunk
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
_MAX_TOPICS = 15


def _slugify(title: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (title or "topic").lower()).strip("-") or "topic"
    base = base[:48]
    key = base
    i = 2
    while key in taken:
        key = f"{base}-{i}"
        i += 1
    taken.add(key)
    return key


def _parse_topics(raw: str) -> list[dict[str, str]]:
    """Extract a JSON array of {title, summary} from an LLM response (tolerant)."""
    out: list[dict[str, str]] = []
    for item in extract_json_array(raw):
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        out.append({"title": title[:120], "summary": str(item.get("summary") or "").strip()[:300]})
    return out


def _finalize(topics: list[dict[str, str]]) -> list[dict[str, str]]:
    taken: set[str] = set()
    seen_titles: set[str] = set()
    result: list[dict[str, str]] = []
    for t in topics:
        norm = t["title"].lower()
        if norm in seen_titles:
            continue
        seen_titles.add(norm)
        result.append({"key": _slugify(t["title"], taken), "title": t["title"], "summary": t.get("summary", "")})
        if len(result) >= _MAX_TOPICS:
            break
    return result


async def generate_topic_outline(db: Session, document_id: uuid.UUID) -> list[dict[str, str]]:
    """Scan the whole document's chunks and return an ordered topic outline."""
    chunk_texts = load_document_chunk_texts(db, document_id)
    if not chunk_texts:
        return []

    from app.services.llm_registry import default_chat_model_id

    model_id = default_chat_model_id(db)
    system = get_prompt(db, "topics_extract_system")
    body = "\n\n".join(chunk_texts)

    if count_tokens(body) <= _SINGLE_SHOT_MAX_TOKENS:
        from app.services.chunk_map_cache import content_hash_key
        from app.services.generation_cache import get as cache_get, put as cache_put

        single_key = content_hash_key(
            "topics_extract", system, truncate_to_tokens(body, _SINGLE_SHOT_MAX_TOKENS), str(model_id)
        )
        hit = cache_get(db, kind="topics_extract", cache_key=single_key)
        if isinstance(hit, str) and hit.strip():
            return _finalize(_parse_topics(hit))
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Document:\n\n{truncate_to_tokens(body, _SINGLE_SHOT_MAX_TOKENS)}"},
        ]
        raw = await complete_chat(messages, db, log_tag="topics_extract", model_id=model_id)
        out = _finalize(_parse_topics(raw))
        if out:
            cache_put(db, kind="topics_extract", cache_key=single_key, value=raw)
        return out

    # Map: topics per chunk; Reduce: merge into one outline.
    sem = asyncio.Semaphore(_MAP_CONCURRENCY)

    async def _map_one(text: str) -> list[dict[str, str]]:
        async with sem:
            raw = await map_chunk_cached(
                db,
                text,
                system=system,
                prompt_key="topics_map:v1",
                user_content="Section:\n\n{excerpt}",
                log_tag="topics_extract",
                model_id=model_id,
                max_input_tokens=_CHUNK_MAP_MAX_TOKENS,
            )
            return _parse_topics(raw)

    partials = await asyncio.gather(*[_map_one(t) for t in chunk_texts])
    merged: list[dict[str, str]] = [t for group in partials for t in group]
    if not merged:
        return []

    rollup_system = get_prompt(db, "topics_rollup_system")
    listing = truncate_to_tokens(
        "\n".join(f"- {t['title']}: {t.get('summary', '')}" for t in merged), _ROLLUP_INPUT_MAX_TOKENS
    )
    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get, put as cache_put

    rollup_key = content_hash_key("topics_rollup", rollup_system, listing, str(model_id))
    hit = cache_get(db, kind="topics_rollup", cache_key=rollup_key)
    if isinstance(hit, str) and hit.strip():
        rolled = _parse_topics(hit)
        return _finalize(rolled or merged)
    raw = await complete_chat(
        [
            {"role": "system", "content": rollup_system},
            {"role": "user", "content": f"Partial topic lists:\n\n{listing}"},
        ],
        db,
        log_tag="topics_rollup",
        model_id=model_id,
    )
    rolled = _parse_topics(raw)
    if rolled:
        cache_put(db, kind="topics_rollup", cache_key=rollup_key, value=raw)
    return _finalize(rolled or merged)


_EXPLAIN_CONTEXT_MAX_TOKENS = SUMMARIZE_ROLLUP_INPUT_MAX_TOKENS


async def explain_topic(
    db: Session, document_id: uuid.UUID, *, title: str, summary: str
) -> str:
    """Generate a high-level, plain-language, step-by-step explanation of one topic.

    Grounds the explanation in the document's own chunks (RAG top-k by similarity to
    the topic), falling back to the leading chunks if vector search is unavailable.
    """
    from app.services.embed import embed_query
    from app.services.llm_registry import default_chat_model_id
    from app.services.retrieval import search_chunks

    query = f"{title}. {summary}".strip()
    chunks: list[dict] = []
    try:
        chunks = search_chunks(
            db, document_ids=[document_id], query_embedding=embed_query(query), limit=8
        )
    except Exception:
        chunks = []
    if chunks:
        context = "\n\n".join(c["text"] for c in chunks if c.get("text"))
    else:
        rows = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.page_start.asc())
            .limit(8)
            .all()
        )
        context = "\n\n".join(r.text for r in rows if r.text)
    if not context.strip():
        return ""

    context = truncate_to_tokens(context, _EXPLAIN_CONTEXT_MAX_TOKENS)
    system = get_prompt(db, "topic_explain_system")
    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get, put as cache_put

    explain_key = content_hash_key(
        "topic_explain", system, context, title, summary, str(default_chat_model_id(db))
    )
    hit = cache_get(db, kind="topic_explain", cache_key=explain_key)
    if isinstance(hit, str) and hit.strip():
        return hit.strip()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"SOURCE EXCERPTS:\n\n{context}"},
        {
            "role": "user",
            "content": (
                "Explain this topic at a high level, step by step, in plain language:\n\n"
                f'Topic: "{title}"\n{summary}'
            ),
        },
    ]
    out = await complete_chat(
        messages, db, log_tag="topic_explain", model_id=default_chat_model_id(db)
    )
    if not (out or "").strip():
        # Intermittent empty completion — retry once via the pool (failover-capable).
        out = await complete_chat(messages, db, log_tag="topic_explain", model_id=None)
    out = (out or "").strip()
    if out:
        cache_put(db, kind="topic_explain", cache_key=explain_key, value=out)
    return out
