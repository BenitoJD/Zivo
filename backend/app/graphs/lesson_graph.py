"""Per-page Learn lesson generation (worker-only).

A Learn lesson is short, question-aware teaching prose shown before a page's
MCQs so the learner reads the concept, then answers questions on it. Generated
inside the page cook (before the MCQ loop), so:

  * it is anti-spoiler by construction — the lesson is written from the triaged
    aspects and page text *before* any MCQ stems exist, so it can teach the
    concept but cannot reveal an answer that has not been authored yet;
  * it is on the learner's prep path, not the answer path — the ~3s call lands
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

from app.services.llm_json import extract_json_obj
from app.services.llm_router import acomplete_chat
from app.services.llm_sync import run_coro_in_worker
from app.services.page_lessons import plan_lesson_output_caps
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
    except Exception:
        if model_id is None:
            raise
        return run_coro_in_worker(
            acomplete_chat(messages, db, log_tag="lesson_page", model_id=None)
        )


def _aspects_block(aspects: list[dict[str, Any]] | None) -> str:
    """Render triage aspects as the "- label (angle)" spine the prompt consumes.

    Only ``central`` aspects are taught (supporting/peripheral ones are not
    tested on their own), and the cognitive angle — when present — tells the
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
    if not obj:
        return None
    title = str(obj.get("title") or "").strip()
    body = str(obj.get("body") or "").strip()
    if not body:
        return None
    if not title:
        # Fall back to the first sentence of the body so the heading is never blank.
        title = body.split("\n", 1)[0].split(". ", 1)[0].strip()
    caps = plan_lesson_output_caps()
    return {"title": title[: caps.title_chars], "body": body[: caps.body_chars]}


def generate_lesson(
    db: Session,
    *,
    page_text: str,
    aspects: list[dict[str, Any]],
) -> dict[str, str] | None:
    """Generate one page's lesson prose from the page text and its triaged aspects.

    Returns ``{"title", "body"}`` on success, or ``None`` if the model returned
    nothing usable after one retry. Pure generation — the caller (the page-lesson
    service) owns persistence and lifecycle. Synchronous because it runs inside
    the page cook (a CPU worker); the LLM call is driven via
    :func:`_complete_chat_sync` on the worker's persistent loop.
    """
    from app.services.llm_registry import default_chat_model_id
    from app.services.mcq_dedup import SUBJECT_MATTER_PREFIX
    from app.services.token_budget import PAGE_INPUT_MAX_TOKENS, truncate_to_tokens

    excerpt = truncate_to_tokens(page_text or "", PAGE_INPUT_MAX_TOKENS)
    if not excerpt.strip():
        return None

    system = get_prompt(db, "lesson_page_system")
    instructions = (
        "Write the lesson for this page now, teaching every concept listed below.\n\n"
        f"CONCEPTS THE QUESTIONS WILL TEST:\n{_aspects_block(aspects)}\n\n"
        "Emit ONLY one ```zv-lesson``` JSON block."
    )
    messages = [
        {"role": "system", "content": system},
        # SUBJECT_MATTER_PREFIX marks the page-text message so providers can cache
        # it — the same prefix the MCQ generator uses, so lesson + MCQ calls for
        # one page share the cached prefix.
        {"role": "user", "content": f"{SUBJECT_MATTER_PREFIX}\n{excerpt}"},
        {"role": "user", "content": instructions},
    ]

    model_id = default_chat_model_id(db)
    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get, put as cache_put

    lesson_key = content_hash_key("lesson_page", system, excerpt, _aspects_block(aspects), str(model_id))
    hit = cache_get(db, kind="lesson_page", cache_key=lesson_key)
    if isinstance(hit, str) and hit.strip():
        parsed_hit = _parse_lesson(hit)
        if parsed_hit:
            return parsed_hit

    raw = _complete_chat_sync(db, messages, model_id=model_id)
    parsed = _parse_lesson(raw or "")
    if parsed:
        cache_put(db, kind="lesson_page", cache_key=lesson_key, value=raw)
        return parsed
    # Intermittent empty / malformed completion — retry once via the pool.
    logger.warning("lesson parse failed on first attempt; retrying via pool")
    raw = _complete_chat_sync(db, messages, model_id=None)
    parsed = _parse_lesson(raw or "")
    if parsed:
        cache_put(db, kind="lesson_page", cache_key=lesson_key, value=raw)
    return parsed
