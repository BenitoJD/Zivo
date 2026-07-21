"""LangGraph MCQ grading — check answer → teach (LLM feedback for every answer)."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.services.llm_router import complete_chat
from app.services.mcq_dedup import sanitize_mcq_explanation
from app.services.prompts import get_prompt
from app.services.token_budget import GRADE_CONTEXT_MAX_TOKENS, truncate_to_tokens

logger = logging.getLogger(__name__)

# Hard cap on the (optional) selection-aware coaching LLM call at grade time. Grading
# must feel instant, so if the provider is slow we abandon the richer feedback and use
# the question's pre-generated explanation instead.
GRADE_FEEDBACK_TIMEOUT = int(os.getenv("ZIVO_GRADE_FEEDBACK_TIMEOUT", "12"))


def _sanitize_feedback(text: str) -> str:
    """Light touch-up: strip any stray document/page/passage residue from LLM feedback.

    We deliberately do NOT truncate — the grader prompt controls length, and
    cutting it destroys the teaching prose.
    """
    return sanitize_mcq_explanation((text or "").strip())


class McqGradeState(TypedDict, total=False):
    question: str
    options: list[str]
    correct_index: int
    selected_index: int
    explanation: str
    aspect_label: str
    document_context: str
    is_correct: bool
    feedback: str
    feedback_ready: bool
    # Populated only for multi-select ("select all that apply") items.
    correct_indices: list[int]
    selected_indices: list[int]


def _check_answer(state: McqGradeState) -> dict[str, Any]:
    """Decide correctness instantly (index compare); always defer feedback to the LLM."""
    return {
        "is_correct": int(state["selected_index"]) == int(state["correct_index"]),
        # Always route to the teach node so feedback is freshly generated.
        "feedback_ready": False,
    }


def try_grade_mcq_fast(
    *,
    options: list[str],
    correct_index: int,
    selected_index: int,
    explanation: str = "",
) -> dict[str, Any] | None:
    """Correctness-only fast path for the API layer.

    Returns the cheap binary verdict plus an EMPTY feedback string. The caller
    (the graph / ``grade_mcq_answer``) is then expected to run the LLM teach
    node to write the world-class feedback. Returning ``feedback=""`` keeps the
    request/response contract while signalling "feedback still pending".
    """
    is_correct = int(selected_index) == int(correct_index)
    correct = options[correct_index] if correct_index < len(options) else ""
    chosen = options[selected_index] if selected_index < len(options) else ""

    # No need for the LLM when there's nothing meaningful to say — but only when
    # the request lacks options entirely (defensive). Otherwise always teach.
    if not options or not correct or not chosen:
        return None
    return {"is_correct": is_correct, "feedback": ""}


def _labels(options: list[str], indices: list[int]) -> str:
    """Human-readable list of option texts for the given indices."""
    picked = [options[i] for i in indices if 0 <= i < len(options)]
    return "; ".join(picked) if picked else "(nothing)"


async def _teach_feedback(state: McqGradeState, *, db: Session) -> dict[str, Any]:
    options = state.get("options") or []
    correct_indices = state.get("correct_indices") or []
    is_multi = len(correct_indices) >= 2
    if is_multi:
        selected_indices = state.get("selected_indices") or []
        correct = _labels(options, sorted(correct_indices))
        chosen = _labels(options, sorted(selected_indices))
    else:
        correct = options[state["correct_index"]] if state["correct_index"] < len(options) else ""
        chosen = options[state["selected_index"]] if state["selected_index"] < len(options) else ""
    is_correct = bool(state.get("is_correct"))
    aspect = (state.get("aspect_label") or "").strip()
    explanation = (state.get("explanation") or "").strip()

    system = get_prompt(db, "mcq_grader_system")
    outcome = "The learner answered CORRECTLY." if is_correct else "The learner answered INCORRECTLY."
    user_lines = [
        f"Question: {state.get('question', '')}",
        "Options:",
        *[f"{i}. {opt}" for i, opt in enumerate(options)],
        outcome,
        ("This is a select-all-that-apply question with more than one correct option."
         if is_multi else ""),
        f"Learner chose: {chosen}",
        f"Correct answer{'s' if is_multi else ''}: {correct}",
    ]
    user_lines = [ln for ln in user_lines if ln]
    if aspect:
        user_lines.append(f"Concept being tested: {aspect}")
    if explanation:
        user_lines.append(f"Author explanation (ground truth — stay faithful to it): {explanation}")
    else:
        user_lines.append("Author explanation: (none provided)")
    user = "\n".join(user_lines)

    if is_correct:
        user += (
            "\n\nThey were right. Write the two-paragraph feedback described in your "
            "instructions (lead = the idea that makes it stick; second paragraph = a "
            "short note on why it matters or a trap to avoid next time)."
        )
    else:
        user += (
            f"\n\nThey were wrong — they picked \"{chosen}\". Your second paragraph MUST "
            f"address that specific choice: name it, explain the misconception it embodies, "
            f"and give the one sentence that separates it from the correct answer "
            f"(\"{correct}\"). Make the mistake click."
        )
    user += (
        "\n\nReturn ONLY the feedback prose — two paragraphs separated by a blank line, "
        "no labels, no markdown, no preamble."
    )

    messages: list[dict] = [{"role": "system", "content": system}]
    doc_ctx = (state.get("document_context") or "").strip()
    if doc_ctx:
        messages.append(
            {
                "role": "user",
                "content": (
                    "Grounding context (authoritative facts for this question — use only to stay "
                    "accurate, never quote it or mention a document):\n"
                    + truncate_to_tokens(doc_ctx, GRADE_CONTEXT_MAX_TOKENS)
                ),
            }
        )
    messages.append({"role": "user", "content": user})

    try:
        feedback = await complete_chat(messages, db, log_tag="grade_mcq")
    except Exception:
        # Last-resort fallback so a provider hiccup never breaks grading.
        feedback = _fallback_feedback(is_correct=is_correct, correct=correct, chosen=chosen)
    return {"feedback": _sanitize_feedback(feedback) or feedback.strip()}


def _fallback_feedback(*, is_correct: bool, correct: str, chosen: str) -> str:
    """Used only if the LLM call throws — keeps grading functional, never user-facing by design."""
    if is_correct:
        return f"The right answer is {correct} — well reasoned."
    return f"The right answer is {correct}. You picked {chosen}, which is the common trap here."


def build_mcq_grade_graph():
    graph = StateGraph(McqGradeState)

    async def teach_node(state: McqGradeState, config) -> dict[str, Any]:
        configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
        db: Session = configurable["db"]
        return await _teach_feedback(state, db=db)

    graph.add_node("check", _check_answer)
    graph.add_node("teach", teach_node)

    def route(state: McqGradeState) -> Literal["done", "teach"]:
        if state.get("feedback_ready"):
            return "done"
        return "teach"

    graph.add_edge(START, "check")
    graph.add_conditional_edges("check", route, {"done": END, "teach": "teach"})
    graph.add_edge("teach", END)
    return graph.compile()


_mcq_grade_graph = None


def get_mcq_grade_graph():
    global _mcq_grade_graph
    if _mcq_grade_graph is None:
        _mcq_grade_graph = build_mcq_grade_graph()
    return _mcq_grade_graph


async def grade_mcq_answer(
    db: Session,
    *,
    question: str,
    options: list[str],
    correct_index: int,
    selected_index: int,
    explanation: str = "",
    aspect_label: str = "",
    document_context: str = "",
    correct_indices: list[int] | None = None,
    selected_indices: list[int] | None = None,
    option_feedback: dict[str, str] | None = None,
) -> dict[str, Any]:
    # Multi-select ("select all that apply") is graded all-or-nothing: the chosen
    # set must exactly equal the correct set. Single-best-answer stays a fast index
    # compare. correct_index (= first correct) is kept for the feedback/return path.
    is_multi = bool(correct_indices) and len(correct_indices) >= 2
    if is_multi:
        is_correct = set(selected_indices or []) == set(correct_indices or [])
    else:
        is_correct = int(selected_index) == int(correct_index)

    # Precomputed per-option coaching (option #4): if this question was coached at
    # generation time (or warmed by an earlier miss), the feedback for the chosen
    # option is already written — return it instantly with ZERO LLM calls. Only the
    # single-best-answer path is precomputed; multi-select depends on the chosen set.
    if not is_multi and option_feedback:
        cached = option_feedback.get(str(selected_index))
        if isinstance(cached, str) and cached.strip():
            return {"is_correct": is_correct, "feedback": cached.strip(), "feedback_source": "precomputed"}

    # Correctness is known instantly from correct_index — the LLM only writes the
    # selection-aware coaching. Bound that call tightly and NEVER let a slow/erroring
    # provider make grading hang or 500: on timeout/failure fall back to the question's
    # own pre-generated explanation (or a generic line). Grading always returns fast.
    feedback = ""
    feedback_source = "llm"
    try:
        graph = get_mcq_grade_graph()
        state: McqGradeState = {
            "question": question,
            "options": options,
            "correct_index": correct_index,
            "selected_index": selected_index,
            "explanation": explanation,
            "aspect_label": aspect_label,
            "document_context": document_context,
            "is_correct": is_correct,
        }
        if is_multi:
            state["correct_indices"] = list(correct_indices or [])
            state["selected_indices"] = list(selected_indices or [])
        result = await asyncio.wait_for(
            graph.ainvoke(state, config={"configurable": {"db": db}}),
            timeout=GRADE_FEEDBACK_TIMEOUT,
        )
        feedback = (result.get("feedback") or "").strip()
    except Exception:
        logger.warning("grade feedback generation failed/timed out; using fallback", exc_info=True)

    if not feedback:
        if is_multi:
            fb_correct = _labels(options, sorted(correct_indices or []))
            fb_chosen = _labels(options, sorted(selected_indices or []))
        else:
            fb_correct = options[correct_index] if correct_index < len(options) else ""
            fb_chosen = options[selected_index] if selected_index < len(options) else ""
        feedback = (explanation or "").strip() or _fallback_feedback(
            is_correct=is_correct,
            correct=fb_correct,
            chosen=fb_chosen,
        )
        feedback_source = "fallback"
    return {
        "is_correct": is_correct,
        "feedback": feedback,
        # "precomputed" (instant cache hit) | "llm" (fresh live gen — caller may
        # persist it back to warm the cache) | "fallback" (explanation/generic; do
        # NOT cache, it isn't selection-aware coaching).
        "feedback_source": feedback_source,
    }


def _persist_option_feedback(
    db: Session, assertion_id: uuid.UUID, option_index: int, feedback: str
) -> None:
    """Warm the cache: store one option's freshly-generated coaching in the payload.

    Best-effort and self-contained (own commit) so a warm-up write can never fail
    or roll back the surrounding grade transaction. Concurrent writes for different
    options merge cleanly (jsonb_set is per-key); same-option races are harmless.
    """
    if not (feedback or "").strip():
        return
    from sqlalchemy import text

    try:
        # Merge into option_feedback, creating the object if absent. A nested
        # jsonb_set path (ARRAY['option_feedback', idx]) can't create the missing
        # parent object, so build/merge it explicitly instead.
        db.execute(
            text(
                """
                UPDATE intel.assertion
                SET payload = jsonb_set(
                    COALESCE(payload, '{}'::jsonb),
                    '{option_feedback}',
                    COALESCE(payload->'option_feedback', '{}'::jsonb)
                        || jsonb_build_object(CAST(:idx AS text), CAST(:fb AS text)),
                    true
                )
                WHERE id = :id
                """
            ),
            {"id": assertion_id, "idx": str(int(option_index)), "fb": feedback.strip()},
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.debug("option-feedback warm-up write failed", exc_info=True)


def load_grade_payload(db: Session, assertion_id: uuid.UUID) -> dict[str, Any] | None:
    """Load one assertion's payload, or None if it's gone."""
    import json

    from sqlalchemy import text

    row = db.execute(
        text("SELECT payload FROM intel.assertion WHERE id = :id"),
        {"id": assertion_id},
    ).first()
    if not row:
        return None
    return row[0] if isinstance(row[0], dict) else json.loads(row[0])


def _normalized_correct_indices(payload: dict[str, Any]) -> list[int] | None:
    ci = payload.get("correct_indices")
    if isinstance(ci, list) and len(ci) >= 2:
        return [int(i) for i in ci]
    return None


def grade_verdict(
    payload: dict[str, Any], choice_index: int, choice_indices: list[int] | None = None
) -> dict[str, Any]:
    """Instant, LLM-free verdict from the stored payload.

    This is everything the UI needs to reveal the outcome immediately — the LLM
    coaching (``grade_feedback``) is resolved separately and arrives after.
    """
    correct_index = int(payload.get("correct_index", 0))
    correct_indices = _normalized_correct_indices(payload)
    if correct_indices:
        is_correct = set(choice_indices or []) == set(correct_indices)
    else:
        is_correct = int(choice_index) == correct_index
    out: dict[str, Any] = {
        "correct": bool(is_correct),
        "correct_index": correct_index,
        "explanation": sanitize_mcq_explanation(payload.get("explanation") or ""),
    }
    if correct_indices:
        out["correct_indices"] = sorted(correct_indices)
    return out


async def grade_feedback(
    db: Session,
    assertion_id: uuid.UUID,
    payload: dict[str, Any],
    *,
    choice_index: int,
    choice_indices: list[int] | None = None,
) -> str:
    """Selection-aware coaching: instant precomputed hit, else live gen + warm-back."""
    options = payload.get("options") or payload.get("choices") or []
    correct_index = int(payload.get("correct_index", 0))
    correct_indices = _normalized_correct_indices(payload)
    option_feedback = payload.get("option_feedback")
    if not isinstance(option_feedback, dict):
        option_feedback = None

    result = await grade_mcq_answer(
        db,
        question=payload.get("question") or payload.get("stem") or "",
        options=options,
        correct_index=correct_index,
        selected_index=choice_index,
        explanation=payload.get("explanation") or "",
        aspect_label=payload.get("primary_concept") or payload.get("primary_concept_key") or "",
        correct_indices=correct_indices,
        selected_indices=choice_indices,
        option_feedback=option_feedback,
    )
    # Lazy backfill: a fresh single-answer live generation is written back into the
    # assertion's option_feedback map so the next learner to pick this option gets it
    # instantly. Best-effort — a failed warm-up never affects this grade.
    if (
        result.get("feedback_source") == "llm"
        and not correct_indices
        and 0 <= int(choice_index) < len(options)
    ):
        _persist_option_feedback(db, assertion_id, int(choice_index), str(result.get("feedback") or ""))
    return str(result.get("feedback") or "")


def grade_mcq(
    db: Session,
    assertion_id: uuid.UUID,
    choice_index: int,
    *,
    choice_indices: list[int] | None = None,
) -> dict[str, Any]:
    """Combined verdict + feedback (JSON endpoint / practice). Verdict-first
    streaming composes ``grade_verdict`` + ``grade_feedback`` directly instead.
    """
    import asyncio

    payload = load_grade_payload(db, assertion_id)
    if payload is None:
        return {"correct": False, "feedback": "Question not found", "correct_index": 0}
    out = grade_verdict(payload, choice_index, choice_indices)
    out.pop("explanation", None)  # JSON grade response never carried this field
    out["feedback"] = asyncio.run(
        grade_feedback(db, assertion_id, payload, choice_index=choice_index, choice_indices=choice_indices)
    )
    return out
