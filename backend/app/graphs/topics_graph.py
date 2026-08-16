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

from app.engine_runtime import apply, pick
from app.models import DocumentChunk
from app.services.chunk_map_cache import map_chunk_cached
from app.services.chunks import load_document_chunk_texts
from app.services.llm_json import extract_json_array
from app.services.llm_route import evaluate_llm_route
from app.services.llm_router import complete_chat
from app.services.presence import evaluate_presence
from app.services.prompts import get_prompt
from app.services.session_design import (
    plan_auxiliary_artifact_cap,
    plan_auxiliary_field_caps,
    plan_auxiliary_generation_strategy,
    plan_auxiliary_map_concurrency,
)
from app.services.token_budget import (
    SUMMARIZE_CHUNK_INPUT_MAX_TOKENS,
    SUMMARIZE_ROLLUP_INPUT_MAX_TOKENS,
    count_tokens,
    truncate_to_tokens,
)
from app.services.tutor_retrieval import plan_topic_explain_context

_CHUNK_MAP_MAX_TOKENS = SUMMARIZE_CHUNK_INPUT_MAX_TOKENS
_ROLLUP_INPUT_MAX_TOKENS = SUMMARIZE_ROLLUP_INPUT_MAX_TOKENS
_MAX_TOPICS = plan_auxiliary_artifact_cap("topics").max_count


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


def _parse_one_topic(item: object) -> dict[str, str] | None:
    row = pick(isinstance(item, dict), lambda: item, lambda: {})
    title = str(row.get("title") or "").strip()

    def _topic() -> dict[str, str]:
        caps = plan_auxiliary_field_caps("topics")
        return {
            "title": title[: caps.limit("title")],
            "summary": str(row.get("summary") or "").strip()[: caps.limit("summary")],
        }

    return apply(
        evaluate_presence(title).action,
        {"ok": _topic, "empty": lambda: None, "missing": lambda: None},
    )


def _parse_topics(raw: str) -> list[dict[str, str]]:
    """Extract a JSON array of {title, summary} from an LLM response (tolerant)."""
    return list(filter(None, map(_parse_one_topic, extract_json_array(raw))))


def _finalize(topics: list[dict[str, str]]) -> list[dict[str, str]]:
    taken: set[str] = set()
    seen_titles: set[str] = set()

    def _keep(topic: dict[str, str]) -> bool:
        norm = topic["title"].lower()

        def _accept() -> bool:
            seen_titles.add(norm)
            return True

        return pick(norm in seen_titles, lambda: False, _accept)

    kept = list(filter(_keep, topics))[:_MAX_TOPICS]
    return [
        {
            "key": _slugify(t["title"], taken),
            "title": t["title"],
            "summary": t.get("summary", ""),
        }
        for t in kept
    ]


def _cache_text(hit: object) -> str:
    return pick(isinstance(hit, str), lambda: str(hit).strip(), lambda: "")


async def generate_topic_outline(db: Session, document_id: uuid.UUID) -> list[dict[str, str]]:
    """Scan the whole document's chunks and return an ordered topic outline."""
    chunk_texts = load_document_chunk_texts(db, document_id)

    async def _empty() -> list[dict[str, str]]:
        return []

    async def _generate() -> list[dict[str, str]]:
        from app.services.llm_registry import default_chat_model_id

        model_id = default_chat_model_id(db)
        system = get_prompt(db, "topics_extract_system")
        body = "\n\n".join(chunk_texts)
        gen = plan_auxiliary_generation_strategy(count_tokens(body))

        async def _single_shot() -> list[dict[str, str]]:
            from app.services.chunk_map_cache import content_hash_key
            from app.services.generation_cache import get as cache_get, put as cache_put

            single_key = content_hash_key(
                "topics_extract",
                system,
                truncate_to_tokens(body, gen.single_shot_max_tokens),
                str(model_id),
            )
            hit = cache_get(db, kind="topics_extract", cache_key=single_key)
            cached = _cache_text(hit)

            async def _hit() -> list[dict[str, str]]:
                return _finalize(_parse_topics(hit))  # type: ignore[arg-type]

            async def _miss() -> list[dict[str, str]]:
                messages = [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": (
                            "Document:\n\n"
                            f"{truncate_to_tokens(body, gen.single_shot_max_tokens)}"
                        ),
                    },
                ]
                raw = await complete_chat(
                    messages, db, log_tag="topics_extract", model_id=model_id
                )
                out = _finalize(_parse_topics(raw))
                pick(
                    bool(out),
                    lambda: cache_put(
                        db, kind="topics_extract", cache_key=single_key, value=raw
                    ),
                    lambda: None,
                )
                return out

            return await apply(
                evaluate_presence(cached).action,
                {"ok": _hit, "empty": _miss, "missing": _miss},
            )

        async def _map_reduce() -> list[dict[str, str]]:
            sem = asyncio.Semaphore(plan_auxiliary_map_concurrency("topics"))

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

            async def _rollup() -> list[dict[str, str]]:
                rollup_system = get_prompt(db, "topics_rollup_system")
                listing = truncate_to_tokens(
                    "\n".join(f"- {t['title']}: {t.get('summary', '')}" for t in merged),
                    _ROLLUP_INPUT_MAX_TOKENS,
                )
                from app.services.chunk_map_cache import content_hash_key
                from app.services.generation_cache import get as cache_get, put as cache_put

                rollup_key = content_hash_key(
                    "topics_rollup", rollup_system, listing, str(model_id)
                )
                hit = cache_get(db, kind="topics_rollup", cache_key=rollup_key)
                cached = _cache_text(hit)

                def _finalize_rolled(rolled: list[dict[str, str]]) -> list[dict[str, str]]:
                    return _finalize(rolled or merged)

                async def _hit() -> list[dict[str, str]]:
                    return _finalize_rolled(_parse_topics(hit))  # type: ignore[arg-type]

                async def _miss() -> list[dict[str, str]]:
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
                    pick(
                        bool(rolled),
                        lambda: cache_put(
                            db, kind="topics_rollup", cache_key=rollup_key, value=raw
                        ),
                        lambda: None,
                    )
                    return _finalize_rolled(rolled)

                return await apply(
                    evaluate_presence(cached).action,
                    {"ok": _hit, "empty": _miss, "missing": _miss},
                )

            return await apply(
                evaluate_presence(merged).action,
                {"ok": _rollup, "empty": _empty, "missing": _empty},
            )

        return await apply(gen.mode, {"single_shot": _single_shot, "map_reduce": _map_reduce})

    return await apply(
        evaluate_presence(chunk_texts).action,
        {"missing": _empty, "empty": _empty, "ok": _generate},
    )


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
    ctx = plan_topic_explain_context()
    try:
        chunks = search_chunks(
            db,
            document_ids=[document_id],
            query_embedding=embed_query(query),
            limit=ctx.chunk_limit,
        )
    except Exception:
        chunks = []

    def _from_hits() -> str:
        return "\n\n".join(
            c["text"] for c in filter(lambda c: c.get("text"), chunks)
        )

    def _from_leading() -> str:
        rows = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.page_start.asc())
            .limit(ctx.chunk_limit)
            .all()
        )
        return "\n\n".join(r.text for r in filter(lambda r: r.text, rows))

    context = pick(bool(chunks), _from_hits, _from_leading)

    async def _empty() -> str:
        return ""

    async def _explain() -> str:
        excerpt = truncate_to_tokens(context, _EXPLAIN_CONTEXT_MAX_TOKENS)
        system = get_prompt(db, "topic_explain_system")
        from app.services.chunk_map_cache import content_hash_key
        from app.services.generation_cache import get as cache_get, put as cache_put

        explain_key = content_hash_key(
            "topic_explain", system, excerpt, title, summary, str(default_chat_model_id(db))
        )
        hit = cache_get(db, kind="topic_explain", cache_key=explain_key)
        cached = _cache_text(hit)

        async def _hit() -> str:
            return cached

        async def _miss() -> str:
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": f"SOURCE EXCERPTS:\n\n{excerpt}"},
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
            route = evaluate_llm_route(
                has_primary=True,
                has_fallback=True,
                primary_failed=not (out or "").strip(),
            )

            async def _keep() -> str:
                return out or ""

            async def _fallback() -> str:
                return await complete_chat(messages, db, log_tag="topic_explain", model_id=None)

            out = await apply(
                route.action,
                {"use_primary": _keep, "use_fallback": _fallback, "skip": _keep},
            )
            out = (out or "").strip()
            pick(
                bool(out),
                lambda: cache_put(db, kind="topic_explain", cache_key=explain_key, value=out),
                lambda: None,
            )
            return out

        return await apply(
            evaluate_presence(cached).action,
            {"ok": _hit, "empty": _miss, "missing": _miss},
        )

    return await apply(
        evaluate_presence(context.strip()).action,
        {"ok": _explain, "empty": _empty, "missing": _empty},
    )
