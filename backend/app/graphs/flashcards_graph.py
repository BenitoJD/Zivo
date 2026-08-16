"""Active-recall flashcard generation (worker-only).

Scribely-style: turn a whole source into a deck of flashcards for active recall:
straight Q/A and fill-in-the-blank (cloze) cards. Mirrors topics_graph's map-reduce
over the document's RAG chunks (no new ingest) and its tolerant JSON parsing.

Each card: ``{"front", "back", "kind"}`` where kind is "qa" or "cloze".
Off the answer path: runs in a background worker.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.orm import Session

from app.engine_runtime import apply, choose, pick
from app.services.chunk_map_cache import map_chunk_cached
from app.services.chunks import load_document_chunk_texts
from app.services.llm_json import extract_json_array
from app.services.llm_router import complete_chat
from app.services.llm_route import evaluate_llm_route
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

_CHUNK_MAP_MAX_TOKENS = SUMMARIZE_CHUNK_INPUT_MAX_TOKENS
_ROLLUP_INPUT_MAX_TOKENS = SUMMARIZE_ROLLUP_INPUT_MAX_TOKENS
_MAX_CARDS = plan_auxiliary_artifact_cap("flashcards").max_count


def _parse_one_card(item: object) -> dict[str, str] | None:
    row = pick(isinstance(item, dict), lambda: item, lambda: {})
    front = str(row.get("front") or row.get("question") or "").strip()
    back = str(row.get("back") or row.get("answer") or "").strip()

    def _card() -> dict[str, str]:
        kind = str(row.get("kind") or "qa").strip().lower()
        kind = choose(kind in ("qa", "cloze"), kind, "qa")
        caps = plan_auxiliary_field_caps("flashcards")
        return {
            "front": front[: caps.limit("front")],
            "back": back[: caps.limit("back")],
            "kind": kind,
        }

    return apply(
        evaluate_presence(front and back).action,
        {"ok": _card, "empty": lambda: None, "missing": lambda: None},
    )


def _parse_cards(raw: str) -> list[dict[str, str]]:
    """Extract a JSON array of {front, back, kind} from an LLM response (tolerant)."""
    return list(filter(None, map(_parse_one_card, extract_json_array(raw))))


def _finalize(cards: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()

    def _keep(card: dict[str, str]) -> bool:
        norm = card["front"].lower().strip()

        def _accept() -> bool:
            seen.add(norm)
            return True

        return pick(norm in seen, lambda: False, _accept)

    return list(filter(_keep, cards))[:_MAX_CARDS]


def _cache_text(hit: object) -> str:
    return pick(isinstance(hit, str), lambda: str(hit).strip(), lambda: "")


async def generate_flashcards(db: Session, document_id: uuid.UUID) -> list[dict[str, str]]:
    """Scan the whole document's chunks and return a deck of active-recall cards."""
    chunk_texts = load_document_chunk_texts(db, document_id)

    async def _empty() -> list[dict[str, str]]:
        return []

    async def _generate() -> list[dict[str, str]]:
        from app.services.llm_registry import default_chat_model_id

        model_id = default_chat_model_id(db)
        system = get_prompt(db, "flashcards_system")
        body = "\n\n".join(chunk_texts)
        gen = plan_auxiliary_generation_strategy(count_tokens(body))

        async def _single_shot() -> list[dict[str, str]]:
            from app.services.chunk_map_cache import content_hash_key
            from app.services.generation_cache import get as cache_get, put as cache_put

            single_key = content_hash_key(
                "flashcards_generate",
                system,
                truncate_to_tokens(body, gen.single_shot_max_tokens),
                str(model_id),
            )
            hit = cache_get(db, kind="flashcards_generate", cache_key=single_key)
            cached = _cache_text(hit)

            async def _hit() -> list[dict[str, str]]:
                return _finalize(_parse_cards(hit))  # type: ignore[arg-type]

            async def _miss() -> list[dict[str, str]]:
                messages = [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": (
                            f"SOURCE:\n\n{truncate_to_tokens(body, gen.single_shot_max_tokens)}"
                        ),
                    },
                ]

                async def _call(mid: uuid.UUID | None) -> tuple[str, list[dict[str, str]]]:
                    raw = await complete_chat(
                        messages, db, log_tag="flashcards_generate", model_id=mid
                    )
                    return raw, _finalize(_parse_cards(raw))

                raw, cards = await _call(model_id)
                route = evaluate_llm_route(
                    has_primary=True, has_fallback=True, primary_failed=not cards
                )

                async def _keep() -> tuple[str, list[dict[str, str]]]:
                    return raw, cards

                async def _fallback() -> tuple[str, list[dict[str, str]]]:
                    return await _call(None)

                raw, cards = await apply(
                    route.action,
                    {"use_primary": _keep, "use_fallback": _fallback, "skip": _keep},
                )
                pick(
                    bool(cards),
                    lambda: cache_put(
                        db, kind="flashcards_generate", cache_key=single_key, value=raw
                    ),
                    lambda: None,
                )
                return cards

            return await apply(
                evaluate_presence(cached).action,
                {"ok": _hit, "empty": _miss, "missing": _miss},
            )

        async def _map_reduce() -> list[dict[str, str]]:
            sem = asyncio.Semaphore(plan_auxiliary_map_concurrency("flashcards"))

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

        return await apply(gen.mode, {"single_shot": _single_shot, "map_reduce": _map_reduce})

    return await apply(
        evaluate_presence(chunk_texts).action,
        {"missing": _empty, "empty": _empty, "ok": _generate},
    )
