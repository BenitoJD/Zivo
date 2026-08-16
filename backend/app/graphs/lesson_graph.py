"""Per-page Learn lesson generation (worker-only).

A Learn lesson is short, question-aware teaching prose shown before a page's
MCQs so the learner reads the concept, then answers questions on it. Generated
inside the page cook (before the MCQ loop), so:

  * it is anti-spoiler by construction: the lesson is written from the triaged
    aspects and page text *before* any MCQ stems exist, so it can teach the
    concept but cannot reveal an answer that has not been authored yet;
  * it is on the learner's prep path, not the answer path: the ~3s call lands
    while the learner is still reading or the first MCQ is finishing its
    critique pass.

Output is parsed from one ```` ```zv-lesson ```` JSON block ``{"title", "body"}``,
mirroring the ``zv-mcq`` output idiom and the tolerant parse used by the topic
explanation graph. The generator is a plain coroutine (no I/O of its own beyond
the LLM call); persistence + lifecycle live in :mod:`app.services.page_lessons`.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.engine_runtime import apply, pick
from app.services.llm_json import extract_json_obj
from app.services.llm_route import evaluate_llm_route
from app.services.llm_router import acomplete_chat
from app.services.llm_sync import run_coro_in_worker
from app.services.page_lessons import plan_lesson_output_caps
from app.services.presence import evaluate_presence
from app.services.prompts import get_prompt

logger = logging.getLogger(__name__)

# Policy version stamped into the persisted lesson payload. Bump when the prompt
# voice/structure changes in a way that should invalidate older lessons.
LESSON_POLICY_VERSION = "qb.lesson.v1"


def _complete_chat_sync(
    db: Session, messages: list[dict], *, model_id: uuid.UUID | None = None
) -> str:
    """Run the async LLM call on this worker thread's persistent loop.

    Mirrors ``mcq_quality._complete_chat_sync`` and ``page_triage_graph._complete_chat_sync``:
    pin to one model for cache hit-rate, fall back to the pool on error so a
    stalled provider never fails the cook. The lesson generator runs inside the
    page cook (a CPU worker), so it must acquire its own LLM slot via
    ``run_coro_in_worker`` rather than ``asyncio.run`` (which would replace the
    thread's loop and double-acquire the slot).
    """
    try:
        return run_coro_in_worker(
            acomplete_chat(messages, db, log_tag="lesson_page", model_id=model_id)
        )
    except Exception as err:
        caught = err

        def _reraise() -> str:
            raise caught

        return apply(
            evaluate_llm_route(
                has_primary=model_id is not None,
                has_fallback=model_id is not None,
                primary_failed=True,
            ).action,
            {
                "use_primary": _reraise,
                "use_fallback": lambda: run_coro_in_worker(
                    acomplete_chat(messages, db, log_tag="lesson_page", model_id=None)
                ),
                "skip": _reraise,
            },
        )


def _aspects_block(aspects: list[dict[str, Any]] | None) -> str:
    """Render triage aspects as the "- label (angle)" spine the prompt consumes.

    Only ``central`` aspects are taught (supporting/peripheral ones are not
    tested on their own), and the cognitive angle, when present, tells the
    model what *kind* of understanding to teach (mechanism vs recall vs apply).
    The triage aspect shape carries both ``centrality`` ("central"|"support"|"skip")
    and a derived ``central`` bool; we skip anything that is not central.
    """
    from app.services.page_lessons import plan_lesson_aspects

    return plan_lesson_aspects(aspects).as_block()


def _parse_lesson(raw: str) -> dict[str, str] | None:
    """Tolerant parse of one ```` ```zv-lesson ```` block into ``{title, body}``.

    Returns ``None`` when no usable body survived extraction (caller retries or
    marks the row failed). Truncates over-long fields to the caps above.
    """
    obj = extract_json_obj(raw)

    def _from_obj() -> dict[str, str] | None:
        title = str(obj.get("title") or "").strip()
        body = str(obj.get("body") or "").strip()

        def _built() -> dict[str, str]:
            resolved = pick(
                not title,
                lambda: body.split("\n", 1)[0].split(". ", 1)[0].strip(),
                lambda: title,
            )
            caps = plan_lesson_output_caps()
            return {"title": resolved[: caps.title_chars], "body": body[: caps.body_chars]}

        return apply(
            evaluate_presence(body).action,
            {"ok": _built, "empty": lambda: None, "missing": lambda: None},
        )

    return apply(
        evaluate_presence(obj).action,
        {"ok": _from_obj, "empty": lambda: None, "missing": lambda: None},
    )


def generate_lesson(
    db: Session,
    *,
    page_text: str,
    aspects: list[dict[str, Any]],
) -> dict[str, str] | None:
    """Generate one page's lesson prose from the page text and its triaged aspects.

    Returns ``{"title", "body"}`` on success, or ``None`` if the model returned
    nothing usable after one retry. Pure generation: the caller (the page-lesson
    service) owns persistence and lifecycle. Synchronous because it runs inside
    the page cook (a CPU worker); the LLM call is driven via
    :func:`_complete_chat_sync` on the worker's persistent loop.
    """
    from app.services.llm_registry import default_chat_model_id
    from app.services.mcq_dedup import SUBJECT_MATTER_PREFIX
    from app.services.token_budget import PAGE_INPUT_MAX_TOKENS, truncate_to_tokens

    excerpt = truncate_to_tokens(page_text or "", PAGE_INPUT_MAX_TOKENS)

    def _none() -> dict[str, str] | None:
        return None

    def _generate() -> dict[str, str] | None:
        system = get_prompt(db, "lesson_page_system")
        instructions = (
            "Write the lesson for this page now, teaching every concept listed below.\n\n"
            f"CONCEPTS THE QUESTIONS WILL TEST:\n{_aspects_block(aspects)}\n\n"
            "Emit ONLY one ```zv-lesson``` JSON block."
        )
        messages = [
            {"role": "system", "content": system},
            # SUBJECT_MATTER_PREFIX marks the page-text message so providers can cache
            # it: the same prefix the MCQ generator uses, so lesson + MCQ calls for
            # one page share the cached prefix.
            {"role": "user", "content": f"{SUBJECT_MATTER_PREFIX}\n{excerpt}"},
            {"role": "user", "content": instructions},
        ]

        model_id = default_chat_model_id(db)
        from app.services.chunk_map_cache import content_hash_key
        from app.services.generation_cache import get as cache_get, put as cache_put

        lesson_key = content_hash_key(
            "lesson_page", system, excerpt, _aspects_block(aspects), str(model_id)
        )
        hit = cache_get(db, kind="lesson_page", cache_key=lesson_key)

        def _from_cache() -> dict[str, str] | None:
            parsed_hit = _parse_lesson(hit)  # type: ignore[arg-type]
            return pick(bool(parsed_hit), lambda: parsed_hit, _call_llm)

        def _store(raw: str, parsed: dict[str, str] | None) -> dict[str, str] | None:
            pick(
                bool(parsed),
                lambda: cache_put(db, kind="lesson_page", cache_key=lesson_key, value=raw),
                lambda: None,
            )
            return parsed

        def _call_llm() -> dict[str, str] | None:
            raw = _complete_chat_sync(db, messages, model_id=model_id)
            parsed = _parse_lesson(raw or "")

            def _retry() -> dict[str, str] | None:
                logger.warning("lesson parse failed on first attempt; retrying via pool")
                raw2 = _complete_chat_sync(db, messages, model_id=None)
                parsed2 = _parse_lesson(raw2 or "")
                return _store(raw2, parsed2)

            return apply(
                evaluate_presence(parsed).action,
                {
                    "ok": lambda: _store(raw, parsed),
                    "empty": _retry,
                    "missing": _retry,
                },
            )

        cached = pick(isinstance(hit, str), lambda: str(hit).strip(), lambda: "")
        return apply(
            evaluate_presence(cached).action,
            {"ok": _from_cache, "empty": _call_llm, "missing": _call_llm},
        )

    return apply(
        evaluate_presence(excerpt.strip()).action,
        {"ok": _generate, "empty": _none, "missing": _none},
    )
