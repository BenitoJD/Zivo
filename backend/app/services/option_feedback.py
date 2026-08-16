"""Precomputed per-option MCQ feedback ("option coaching").

Grading an answer is a pure index compare — the LLM only writes the
selection-aware coaching. That coaching is intrinsic to the (question, option)
pair, not to the learner, so we generate it ONCE per option, off the answer
path, and cache it in the assertion payload under ``option_feedback`` (a map of
option-index-string -> feedback). At grade time a present entry is returned
instantly with zero LLM calls; a miss falls back to a live generation that also
warms the cache.

One batched call produces coaching for every option. Single-best-answer only —
multi-select feedback depends on the chosen SET (combinatorial), so multi items
keep the live grade path.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.services.mcq_parsing import _parse_mcq_json
from app.services.session_design import (
    evaluate_option_coach_eligible,
    plan_option_coach_page_limit,
)

logger = logging.getLogger(__name__)

# Own log tag so the router caps output + we can measure coach spend separately.
COACH_LOG_TAG = "coach_mcq"

_COACH_SYSTEM = """You are Zivo — a calm, warm tutor sitting beside a learner. You want them to understand, not just be told they're right.

You are given ONE multiple-choice question, its options, which option is correct, and the author's explanation. Write the feedback the learner should see for EACH option, as if they had just chosen it.

- For the CORRECT option: one or two plain sentences that make the right idea stick — the key insight, stated so clearly they could teach it to a friend, plus (optionally) one note on why it matters.
- For each WRONG option: name the SPECIFIC misconception that option embodies — why it's the tempting trap — and the one sentence that separates it from the right answer. Make the mistake click so they never repeat it.

VOICE
- Speak TO the learner ("you", "your answer"). Be a person, not a textbook. Plain, concrete words, short sentences, warm but never saccharine.
- Do NOT open with "Correct", "Right", "Exactly", "Yes", "Not quite", "Incorrect", or "Sorry" — the UI already shows the outcome. Lead straight into the idea.
- NO markdown (no **, no bullets, no headings). No "the text/passage says". No page numbers, ever.
- Ground every claim in the question, options, and author explanation. Never invent facts.
- Keep each one tight: about 45-80 words.

OUTPUT: exactly ONE JSON object inside a ```json code block. Keys are the option index as a string ("0", "1", ...); values are the feedback string for that option. Include EVERY option index. Nothing outside the block.
Example: {"0": "...", "1": "...", "2": "...", "3": "..."}"""


def _coach_user_message(
    *, question: str, options: list[str], correct_index: int, explanation: str, concept: str
) -> str:
    lines = [
        f"Question: {question}",
        "Options:",
        *[f"{i}. {opt}" for i, opt in enumerate(options)],
        f"Correct option index: {correct_index}",
    ]
    if concept:
        lines.append(f"Concept being tested: {concept}")
    lines.append(
        f"Author explanation (ground truth — stay faithful to it): {explanation}"
        if (explanation or "").strip()
        else "Author explanation: (none provided)"
    )
    lines.append(
        "\nReturn the JSON object mapping every option index to its feedback, per your instructions."
    )
    return "\n".join(lines)


def generate_option_feedback(
    db: Session,
    *,
    question: str,
    options: list[str],
    correct_index: int,
    explanation: str = "",
    concept: str = "",
    model_id=None,
) -> dict[str, str]:
    """One batched LLM call → ``{option_index_str: feedback}`` for every option.

    Single-best-answer only. Returns ``{}`` on any failure (caller keeps whatever
    it had / falls back to the live grade path) — this never raises into a
    generation or grading path.
    """
    # Deferred import avoids a module-load cycle (mcq_quality → llm_router → ...).
    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get, put as cache_put
    from app.services.mcq_quality import _complete_chat_sync

    options = [str(o) for o in (options or [])]
    if len(options) < 2 or not (0 <= int(correct_index) < len(options)):
        return {}

    coach_key = content_hash_key(
        "coach_mcq",
        question or "",
        "|".join(options),
        int(correct_index),
        explanation or "",
        concept or "",
        model_id,
    )
    hit = cache_get(db, kind="coach_mcq", cache_key=coach_key)
    if isinstance(hit, dict) and len(hit) == len(options):
        if all(isinstance(hit.get(str(i)), str) and hit[str(i)].strip() for i in range(len(options))):
            return {str(i): str(hit[str(i)]).strip() for i in range(len(options))}

    messages = [
        {"role": "system", "content": _COACH_SYSTEM},
        {
            "role": "user",
            "content": _coach_user_message(
                question=question or "",
                options=options,
                correct_index=int(correct_index),
                explanation=explanation or "",
                concept=concept or "",
            ),
        },
    ]
    try:
        raw = _complete_chat_sync(db, messages, log_tag=COACH_LOG_TAG, model_id=model_id)
    except Exception:
        logger.warning("option-feedback generation failed", exc_info=True)
        return {}

    parsed = _parse_mcq_json(raw)
    if not isinstance(parsed, dict):
        return {}

    out: dict[str, str] = {}
    for i in range(len(options)):
        val = parsed.get(str(i))
        if isinstance(val, str) and val.strip():
            out[str(i)] = val.strip()
    # Require full coverage — a partial map would silently leave some options on
    # the slow live path forever, which is worse than regenerating cleanly later.
    if len(out) != len(options):
        return {}
    try:
        cache_put(db, kind="coach_mcq", cache_key=coach_key, value=out)
    except Exception:
        logger.debug("option-feedback cache write failed", exc_info=True)
    return out


def _is_single_answer(payload: dict) -> bool:
    ci = payload.get("correct_indices")
    is_multi = isinstance(ci, list) and len(ci) >= 2
    return evaluate_option_coach_eligible(is_multi=is_multi)


def coach_page_assertions(
    db: Session, *, document_id, page_number: int, limit: int | None = None
) -> int:
    """Precompute option feedback for every single-answer MCQ on a page that lacks it.

    Runs off the answer path (background ``coach.mcq_page`` job) so authoring-time
    coaching never delays the streaming first question. Idempotent: only touches
    assertions with no ``option_feedback`` yet, so re-runs / retries are cheap.
    Returns the number of assertions coached.
    """
    import json

    from sqlalchemy import text

    cap = plan_option_coach_page_limit() if limit is None else int(limit)

    rows = db.execute(
        text(
            """
            SELECT id, payload FROM intel.assertion
            WHERE payload->>'artifact_id' = :doc
              AND (payload->>'page_number')::int = :page
              AND status = 'active'
              AND payload ? 'options'
              AND NOT (payload ? 'option_feedback')
            ORDER BY (payload->>'sequence')::int NULLS LAST
            LIMIT :lim
            """
        ),
        {"doc": str(document_id), "page": int(page_number), "lim": cap},
    ).fetchall()

    coached = 0
    for row in rows:
        payload = row[1] if isinstance(row[1], dict) else json.loads(row[1])
        if not _is_single_answer(payload):
            continue
        options = payload.get("options") or payload.get("choices") or []
        fb = generate_option_feedback(
            db,
            question=payload.get("question") or payload.get("stem") or "",
            options=options,
            correct_index=int(payload.get("correct_index", 0)),
            explanation=payload.get("explanation") or "",
            concept=payload.get("primary_concept") or payload.get("primary_concept_key") or "",
        )
        if not fb:
            continue
        try:
            db.execute(
                text(
                    """
                    UPDATE intel.assertion
                    SET payload = jsonb_set(
                        COALESCE(payload, '{}'::jsonb),
                        '{option_feedback}', CAST(:fb AS jsonb), true
                    )
                    WHERE id = :id AND NOT (payload ? 'option_feedback')
                    """
                ),
                {"id": row[0], "fb": json.dumps(fb)},
            )
            db.commit()
            coached += 1
        except Exception:
            db.rollback()
            logger.debug("option-feedback page persist failed for %s", row[0], exc_info=True)
    return coached
