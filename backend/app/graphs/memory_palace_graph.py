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
import re
import uuid

from sqlalchemy.orm import Session

from app.engine_runtime import apply, pick
from app.services.chunk_map_cache import map_chunk_cached
from app.services.chunks import load_document_chunk_texts
from app.services.llm_json import extract_json_obj
from app.services.llm_router import complete_chat
from app.services.presence import evaluate_presence
from app.services.prompts import get_prompt
from app.services.session_design import (
    evaluate_auxiliary_output,
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
_palace_cap = plan_auxiliary_artifact_cap("memory_palace")
_MAX_STATIONS = _palace_cap.max_count
_MIN_STATIONS = _palace_cap.min_count


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


# Tolerant LLM-JSON extraction lives in app.services.llm_json (shared across graphs).
_parse_palace = extract_json_obj


def _station_from_item(item: object, taken: set[str]) -> dict[str, str] | None:
    row = pick(isinstance(item, dict), lambda: item, lambda: {})
    term = str(row.get("term") or "").strip()
    fact = str(row.get("fact") or "").strip()
    image = str(row.get("image") or "").strip()

    def _station() -> dict[str, str]:
        caps = plan_auxiliary_field_caps("memory_palace")
        return {
            "key": _slugify(term, taken),
            "locus": str(row.get("locus") or "").strip()[: caps.limit("locus")],
            "term": term[: caps.limit("term")],
            "fact": fact[: caps.limit("fact")],
            "image": image[: caps.limit("image")],
            "cue": str(row.get("cue") or "").strip()[: caps.limit("cue")],
        }

    return apply(
        evaluate_presence(term and fact and image).action,
        {"ok": _station, "empty": lambda: None, "missing": lambda: None},
    )


def _finalize(data: dict, fallback_setting: str) -> dict:
    raw_stations = data.get("stations")

    def _from_list() -> dict:
        taken: set[str] = set()
        stations = list(
            filter(
                None,
                map(lambda item: _station_from_item(item, taken), raw_stations),
            )
        )[:_MAX_STATIONS]
        keep = evaluate_auxiliary_output("memory_palace", len(stations)).keep
        caps = plan_auxiliary_field_caps("memory_palace")

        def _ok() -> dict:
            return {
                "setting": str(data.get("setting") or fallback_setting or "").strip()[
                    : caps.limit("setting")
                ],
                "intro": str(data.get("intro") or "").strip()[: caps.limit("intro")],
                "stations": stations,
            }

        return pick(keep, _ok, lambda: {})

    return pick(isinstance(raw_stations, list), _from_list, lambda: {})


def _user_msg(setting: str, source: str) -> str:
    place = (setting or "").strip()
    place_line = pick(
        bool(place),
        lambda: f'Use this familiar place as the setting for the journey: "{place}".\n\n',
        lambda: (
            "Choose one ordinary, widely-familiar place for the journey (e.g. walking "
            "through a typical home: front door → hallway → living room → kitchen → ...).\n\n"
        ),
    )
    return f"{place_line}SOURCE:\n\n{source}"


async def generate_memory_palace(
    db: Session, document_id: uuid.UUID, *, setting: str = ""
) -> dict:
    """Scan the document and return a memory-palace journey for the key facts."""
    chunk_texts = load_document_chunk_texts(db, document_id)

    async def _empty() -> dict:
        return {}

    async def _generate() -> dict:
        from app.services.llm_registry import default_chat_model_id

        model_id = default_chat_model_id(db)
        system = get_prompt(db, "memory_palace_system")
        body = "\n\n".join(chunk_texts)
        gen = plan_auxiliary_generation_strategy(count_tokens(body))

        async def _build(source: str) -> dict:
            # The palace JSON (6-8 stations × 5 fields) is large; providers
            # intermittently return empty or truncated completions for it. Try the
            # default model, then the failover pool, then a couple of explicit
            # alternates before giving up: one empty response must not fail the
            # whole build.
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": _user_msg(setting, source)},
            ]
            from app.services.llm_pool import iter_chat_model_attempts

            attempts = [
                model_id,
                None,
                *[
                    m.record.id
                    for m in filter(
                        lambda m: m.record.id != model_id,
                        iter_chat_model_attempts(db, require_vision=False),
                    )
                ],
            ]
            seen: set[str | None] = set()
            found: dict = {}

            async def _skip() -> None:
                return None

            for candidate in attempts:
                already = candidate in seen
                seen.add(candidate)

                async def _attempt(mid: uuid.UUID | None = candidate) -> None:
                    raw = await complete_chat(
                        messages,
                        db,
                        log_tag="memory_palace_generate",
                        model_id=mid,
                    )
                    palace = _finalize(_parse_palace(raw), setting)
                    pick(bool(palace), lambda: found.update(palace), lambda: None)

                await pick(already or bool(found), _skip, _attempt)

            return found

        async def _single_shot() -> dict:
            return await _build(truncate_to_tokens(body, gen.single_shot_max_tokens))

        async def _map_reduce() -> dict:
            fact_system = get_prompt(db, "memory_facts_system")
            sem = asyncio.Semaphore(plan_auxiliary_map_concurrency("memory_palace"))

            async def _facts(text: str) -> str:
                async with sem:
                    return await map_chunk_cached(
                        db,
                        text,
                        system=fact_system,
                        prompt_key="memory_facts_map:v1",
                        user_content="SECTION:\n\n{excerpt}",
                        log_tag="memory_palace_generate",
                        model_id=model_id,
                        max_input_tokens=_CHUNK_MAP_MAX_TOKENS,
                    )

            partials = await asyncio.gather(*[_facts(t) for t in chunk_texts])
            facts = truncate_to_tokens(
                "\n".join(p.strip() for p in filter(lambda p: p.strip(), partials)),
                _ROLLUP_INPUT_MAX_TOKENS,
            )

            async def _from_facts() -> dict:
                return await _build(facts)

            return await apply(
                evaluate_presence(facts.strip()).action,
                {"ok": _from_facts, "empty": _empty, "missing": _empty},
            )

        return await apply(gen.mode, {"single_shot": _single_shot, "map_reduce": _map_reduce})

    return await apply(
        evaluate_presence(chunk_texts).action,
        {"missing": _empty, "empty": _empty, "ok": _generate},
    )
