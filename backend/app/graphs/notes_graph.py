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

from app.engine_runtime import apply, pick
from app.models import Document
from app.services.chunk_map_cache import map_chunk_cached
from app.services.chunks import load_document_chunk_texts
from app.services.llm_router import complete_chat
from app.services.presence import evaluate_presence
from app.services.prompts import get_prompt
from app.services.session_design import (
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

# kind → (map/single-shot prompt key, reduce/rollup prompt key)
_PROMPTS = {
    "notes": ("notes_system", "notes_rollup_system"),
    "cheatsheet": ("cheatsheet_system", "cheatsheet_system"),
}


def _clean(md: str) -> str:
    """Strip a leading ```markdown fence the model sometimes wraps the whole doc in."""
    text = (md or "").strip()

    def _unfence() -> str:
        first_nl = text.find("\n")
        body = pick(first_nl != -1, lambda: text[first_nl + 1 :], lambda: text)
        return pick(
            body.rstrip().endswith("```"),
            lambda: body.rstrip()[:-3],
            lambda: body,
        ).strip()

    return pick(text.startswith("```"), _unfence, lambda: text)


def _cache_text(hit: object) -> str:
    return pick(isinstance(hit, str), lambda: str(hit).strip(), lambda: "")


async def generate_notes(db: Session, document_id: uuid.UUID, *, kind: str = "notes") -> str:
    """Scan the whole document's chunks and return one Markdown study document."""
    kind = pick(kind in _PROMPTS, lambda: kind, lambda: "notes")
    doc = db.get(Document, document_id)

    async def _empty() -> str:
        return ""

    async def _generate() -> str:
        chunk_texts = load_document_chunk_texts(db, document_id)

        async def _from_chunks() -> str:
            from app.services.llm_registry import default_chat_model_id

            model_id = default_chat_model_id(db)
            system_key, rollup_key = _PROMPTS[kind]
            system = get_prompt(db, system_key)
            body = "\n\n".join(chunk_texts)

            title_hint = (doc.meta or {}).get("page_title") or (doc.filename or "this material")
            gen = plan_auxiliary_generation_strategy(count_tokens(body))

            async def _single_shot() -> str:
                from app.services.chunk_map_cache import content_hash_key
                from app.services.generation_cache import get as cache_get, put as cache_put

                single_key = content_hash_key(
                    "notes_generate",
                    kind,
                    system,
                    title_hint,
                    truncate_to_tokens(body, gen.single_shot_max_tokens),
                    str(model_id),
                )
                hit = cache_get(db, kind="notes_generate", cache_key=single_key)
                cached = _cache_text(hit)

                async def _hit() -> str:
                    return _clean(hit)  # type: ignore[arg-type]

                async def _miss() -> str:
                    messages = [
                        {"role": "system", "content": system},
                        {
                            "role": "user",
                            "content": (
                                f'Source title: "{title_hint}"\n\n'
                                f"SOURCE:\n\n{truncate_to_tokens(body, gen.single_shot_max_tokens)}"
                            ),
                        },
                    ]
                    raw = await complete_chat(
                        messages, db, log_tag="notes_generate", model_id=model_id
                    )
                    out = _clean(raw)
                    pick(
                        bool(out),
                        lambda: cache_put(
                            db, kind="notes_generate", cache_key=single_key, value=out
                        ),
                        lambda: None,
                    )
                    return out

                return await apply(
                    evaluate_presence(cached).action,
                    {"ok": _hit, "empty": _miss, "missing": _miss},
                )

            async def _map_reduce() -> str:
                sem = asyncio.Semaphore(plan_auxiliary_map_concurrency("notes"))

                async def _map_one(text: str) -> str:
                    async with sem:
                        raw = await map_chunk_cached(
                            db,
                            text,
                            system=system,
                            prompt_key=f"notes_map:{kind}:v1",
                            user_content=f'SECTION of "{title_hint}":\n\n{{excerpt}}',
                            log_tag="notes_generate",
                            model_id=model_id,
                            max_input_tokens=_CHUNK_MAP_MAX_TOKENS,
                        )
                        return _clean(raw)

                partials = await asyncio.gather(*[_map_one(t) for t in chunk_texts])
                merged = "\n\n".join(filter(None, partials))

                async def _rollup() -> str:
                    rollup_system = get_prompt(db, rollup_key)
                    listing = truncate_to_tokens(merged, _ROLLUP_INPUT_MAX_TOKENS)
                    from app.services.chunk_map_cache import content_hash_key
                    from app.services.generation_cache import get as cache_get, put as cache_put

                    rollup_key_hash = content_hash_key(
                        "notes_rollup", kind, rollup_system, title_hint, listing, str(model_id)
                    )
                    hit = cache_get(db, kind="notes_rollup", cache_key=rollup_key_hash)
                    cached = _cache_text(hit)

                    async def _hit() -> str:
                        return _clean(hit)  # type: ignore[arg-type]

                    async def _miss() -> str:
                        raw = await complete_chat(
                            [
                                {"role": "system", "content": rollup_system},
                                {
                                    "role": "user",
                                    "content": (
                                        f'Source title: "{title_hint}"\n\n'
                                        "Section notes to merge into one clean study document:\n\n"
                                        f"{listing}"
                                    ),
                                },
                            ],
                            db,
                            log_tag="notes_rollup",
                            model_id=model_id,
                        )
                        out = _clean(raw) or _clean(merged)
                        pick(
                            bool(out),
                            lambda: cache_put(
                                db,
                                kind="notes_rollup",
                                cache_key=rollup_key_hash,
                                value=out,
                            ),
                            lambda: None,
                        )
                        return out

                    return await apply(
                        evaluate_presence(cached).action,
                        {"ok": _hit, "empty": _miss, "missing": _miss},
                    )

                return await apply(
                    evaluate_presence(merged.strip()).action,
                    {"ok": _rollup, "empty": _empty, "missing": _empty},
                )

            return await apply(gen.mode, {"single_shot": _single_shot, "map_reduce": _map_reduce})

        return await apply(
            evaluate_presence(chunk_texts).action,
            {"missing": _empty, "empty": _empty, "ok": _from_chunks},
        )

    return await apply(
        evaluate_presence(doc).action,
        {"missing": _empty, "empty": _empty, "ok": _generate},
    )
