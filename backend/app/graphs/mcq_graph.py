"""LangGraph MCQ grading — check answer → teach (LLM feedback for every answer)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.services.llm_router import complete_chat
from app.services.mcq_dedup import sanitize_mcq_explanation
from app.services.open_response import evaluate_mcq_correct, evaluate_mcq_grade_kind
from app.services.presence import evaluate_presence
from app.services.prompts import get_prompt
from app.services.session_design import (
    evaluate_option_coach_eligible,
    plan_grade_feedback_timeout,
)
from app.services.token_budget import GRADE_CONTEXT_MAX_TOKENS, truncate_to_tokens

logger = logging.getLogger(__name__)

GRADE_FEEDBACK_TIMEOUT = plan_grade_feedback_timeout()


def _sanitize_feedback(text: str) -> str:
    """Light touch-up: strip any stray document/page/passage residue from LLM feedback.

    We deliberately do NOT truncate: the grader prompt controls length, and
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
    """Decide correctness instantly; always defer feedback to the LLM.

    Multi-select ("select all that apply") is all-or-nothing on the chosen set.
    Single-best-answer stays a fast index compare. Must not overwrite a correct
    multi-select verdict with a single-index compare: that poisoned LLM coaching.
    """
    correct_indices = state.get("correct_indices") or []
    is_correct = evaluate_mcq_correct(
        is_multi=len(correct_indices) >= 2,
        choice_index=int(state["selected_index"]),
        correct_index=int(state["correct_index"]),
        choice_indices=state.get("selected_indices") or [],
        correct_indices=correct_indices,
    )
    return {
        "is_correct": is_correct,
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
    correct = pick(correct_index < len(options), lambda: options[correct_index], lambda: "")
    chosen = pick(selected_index < len(options), lambda: options[selected_index], lambda: "")

    # No need for the LLM when there's nothing meaningful to say, but only when
    # the request lacks options entirely (defensive). Otherwise always teach.
    return apply(
        evaluate_presence(options and correct and chosen).action,
        {
            "ok": lambda: {"is_correct": is_correct, "feedback": ""},
            "empty": lambda: None,
            "missing": lambda: None,
        },
    )


def _labels(options: list[str], indices: list[int]) -> str:
    """Human-readable list of option texts for the given indices."""
    picked = [
        options[i] for i in filter(lambda i: 0 <= i < len(options), indices)
    ]
    return choose(bool(picked), "; ".join(picked), "(nothing)")


async def _teach_feedback(state: McqGradeState, *, db: Session) -> dict[str, Any]:
    options = state.get("options") or []
    correct_indices = state.get("correct_indices") or []
    is_multi = len(correct_indices) >= 2
    labels = apply(
        evaluate_mcq_grade_kind(is_multi=is_multi),
        {
            "multi": lambda: (
                _labels(options, sorted(correct_indices)),
                _labels(options, sorted(state.get("selected_indices") or [])),
            ),
            "single": lambda: (
                pick(
                    state["correct_index"] < len(options),
                    lambda: options[state["correct_index"]],
                    lambda: "",
                ),
                pick(
                    state["selected_index"] < len(options),
                    lambda: options[state["selected_index"]],
                    lambda: "",
                ),
            ),
        },
    )
    correct, chosen = labels
    is_correct = bool(state.get("is_correct"))
    aspect = (state.get("aspect_label") or "").strip()
    explanation = (state.get("explanation") or "").strip()

    system = get_prompt(db, "mcq_grader_system")
    outcome = choose(
        is_correct,
        "The learner answered CORRECTLY.",
        "The learner answered INCORRECTLY.",
    )
    # Prefix-cache discipline: everything stable per QUESTION leads so calls for
    # different selections of the same question share the provider prefix; the
    # per-selection outcome/choice lines ride at the end.
    user_lines = [
        f"Question: {state.get('question', '')}",
        "Options:",
        *[f"{i}. {opt}" for i, opt in enumerate(options)],
        choose(
            is_multi,
            "This is a select-all-that-apply question with more than one correct option.",
            "",
        ),
        f"Correct answer{choose(is_multi, 's', '')}: {correct}",
    ]
    user_lines = list(filter(None, user_lines))
    pick(bool(aspect), lambda: user_lines.append(f"Concept being tested: {aspect}"), lambda: None)
    pick(
        bool(explanation),
        lambda: user_lines.append(
            f"Author explanation (ground truth: stay faithful to it): {explanation}"
        ),
        lambda: user_lines.append("Author explanation: (none provided)"),
    )
    user_lines.append(outcome)
    user_lines.append(f"Learner chose: {chosen}")
    user = "\n".join(user_lines)
    user += apply(
        first_match(
            (
                Rule(when=(Pred("is_correct", "truthy"),), action="correct"),
                Rule(when=(), action="incorrect"),
            ),
            {"is_correct": is_correct},
        ).action,
        {
            "correct": lambda: (
                "\n\nThey were right. Write the two-paragraph feedback described in your "
                "instructions (lead = the idea that makes it stick; second paragraph = a "
                "short note on why it matters or a trap to avoid next time)."
            ),
            "incorrect": lambda: (
                f"\n\nThey were wrong: they picked \"{chosen}\". Your second paragraph MUST "
                f"address that specific choice: name it, explain the misconception it embodies, "
                f"and give the one sentence that separates it from the correct answer "
                f"(\"{correct}\"). Make the mistake click."
            ),
        },
    )
    user += (
        "\n\nReturn ONLY the feedback prose: two paragraphs separated by a blank line, "
        "no labels, no markdown, no preamble."
    )

    messages: list[dict] = [{"role": "system", "content": system}]
    doc_ctx = (state.get("document_context") or "").strip()

    def _add_grounding() -> None:
        messages.append(
            {
                "role": "user",
                "content": (
                    "Grounding context (authoritative facts for this question: use only to stay "
                    "accurate, never quote it or mention a document):\n"
                    + truncate_to_tokens(doc_ctx, GRADE_CONTEXT_MAX_TOKENS)
                ),
            }
        )

    pick(bool(doc_ctx), _add_grounding, lambda: None)
    messages.append({"role": "user", "content": user})

    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get, put as cache_put

    grade_key = content_hash_key("grade_mcq", system, user)
    hit = cache_get(db, kind="grade_mcq", cache_key=grade_key)
    cached = pick(isinstance(hit, str), lambda: str(hit).strip(), lambda: "")

    async def _from_cache() -> dict[str, Any]:
        return {"feedback": _sanitize_feedback(hit) or str(hit).strip()}  # type: ignore[arg-type]

    async def _from_llm() -> dict[str, Any]:
        try:
            feedback = await complete_chat(messages, db, log_tag="grade_mcq")
        except Exception:
            # Last-resort fallback so a provider hiccup never breaks grading.
            feedback = _fallback_feedback(is_correct=is_correct, correct=correct, chosen=chosen)
        pick(
            bool((feedback or "").strip()),
            lambda: cache_put(db, kind="grade_mcq", cache_key=grade_key, value=feedback),
            lambda: None,
        )
        return {"feedback": _sanitize_feedback(feedback) or feedback.strip()}

    return await apply(
        evaluate_presence(cached).action,
        {"ok": _from_cache, "empty": _from_llm, "missing": _from_llm},
    )


def _fallback_feedback(*, is_correct: bool, correct: str, chosen: str) -> str:
    """Used only if the LLM call throws: keeps grading functional, never user-facing by design."""
    return apply(
        first_match(
            (
                Rule(when=(Pred("is_correct", "truthy"),), action="correct"),
                Rule(when=(), action="incorrect"),
            ),
            {"is_correct": is_correct},
        ).action,
        {
            "correct": lambda: f"The right answer is {correct}: well reasoned.",
            "incorrect": lambda: (
                f"The right answer is {correct}. You picked {chosen}, which is the common trap here."
            ),
        },
    )


def build_mcq_grade_graph():
    graph = StateGraph(McqGradeState)

    async def teach_node(state: McqGradeState, config) -> dict[str, Any]:
        configurable = pick(
            isinstance(config, dict),
            lambda: config.get("configurable", {}),
            lambda: {},
        )
        db: Session = configurable["db"]
        return await _teach_feedback(state, db=db)

    graph.add_node("check", _check_answer)
    graph.add_node("teach", teach_node)

    def route(state: McqGradeState) -> Literal["done", "teach"]:
        return choose(bool(state.get("feedback_ready")), "done", "teach")

    graph.add_edge(START, "check")
    graph.add_conditional_edges("check", route, {"done": END, "teach": "teach"})
    graph.add_edge("teach", END)
    return graph.compile()


_mcq_grade_graph = None


def get_mcq_grade_graph():
    global _mcq_grade_graph

    def _build():
        global _mcq_grade_graph
        _mcq_grade_graph = build_mcq_grade_graph()
        return _mcq_grade_graph

    return pick(_mcq_grade_graph is None, _build, lambda: _mcq_grade_graph)


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
    is_correct = evaluate_mcq_correct(
        is_multi=is_multi,
        choice_index=int(selected_index),
        correct_index=int(correct_index),
        choice_indices=selected_indices or [],
        correct_indices=correct_indices or [],
    )

    cached = pick(
        evaluate_option_coach_eligible(is_multi=is_multi) and bool(option_feedback),
        lambda: (option_feedback or {}).get(str(selected_index)),
        lambda: None,
    )
    cached_text = pick(isinstance(cached, str), lambda: str(cached).strip(), lambda: "")

    async def _precomputed() -> dict[str, Any]:
        return {
            "is_correct": is_correct,
            "feedback": cached_text,
            "feedback_source": "precomputed",
        }

    async def _generate() -> dict[str, Any]:
        # Correctness is known instantly from correct_index: the LLM only writes the
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

            def _stamp_multi() -> None:
                state["correct_indices"] = list(correct_indices or [])
                state["selected_indices"] = list(selected_indices or [])

            pick(is_multi, _stamp_multi, lambda: None)
            result = await asyncio.wait_for(
                graph.ainvoke(state, config={"configurable": {"db": db}}),
                timeout=GRADE_FEEDBACK_TIMEOUT,
            )
            feedback = (result.get("feedback") or "").strip()
        except Exception:
            logger.warning(
                "grade feedback generation failed/timed out; using fallback", exc_info=True
            )

        def _fallback_pair() -> tuple[str, str]:
            pair = apply(
                evaluate_mcq_grade_kind(is_multi=is_multi),
                {
                    "multi": lambda: (
                        _labels(options, sorted(correct_indices or [])),
                        _labels(options, sorted(selected_indices or [])),
                    ),
                    "single": lambda: (
                        pick(
                            correct_index < len(options),
                            lambda: options[correct_index],
                            lambda: "",
                        ),
                        pick(
                            selected_index < len(options),
                            lambda: options[selected_index],
                            lambda: "",
                        ),
                    ),
                },
            )
            fb_correct, fb_chosen = pair
            return (
                (explanation or "").strip()
                or _fallback_feedback(
                    is_correct=is_correct, correct=fb_correct, chosen=fb_chosen
                ),
                "fallback",
            )

        def _keep() -> tuple[str, str]:
            return feedback, feedback_source

        feedback, feedback_source = pick(not (feedback or "").strip(), _fallback_pair, _keep)
        return {
            "is_correct": is_correct,
            "feedback": feedback,
            # "precomputed" (instant cache hit) | "llm" (fresh live gen: caller may
            # persist it back to warm the cache) | "fallback" (explanation/generic; do
            # NOT cache, it isn't selection-aware coaching).
            "feedback_source": feedback_source,
        }

    return await apply(
        evaluate_presence(cached_text).action,
        {"ok": _precomputed, "empty": _generate, "missing": _generate},
    )


def _persist_option_feedback(
    db: Session, assertion_id: uuid.UUID, option_index: int, feedback: str
) -> None:
    """Warm the cache: store one option's freshly-generated coaching in the payload.

    Best-effort and self-contained (own commit) so a warm-up write can never fail
    or roll back the surrounding grade transaction. Concurrent writes for different
    options merge cleanly (jsonb_set is per-key); same-option races are harmless.
    """

    def _write() -> None:
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

    apply(
        evaluate_presence((feedback or "").strip()).action,
        {"ok": _write, "empty": lambda: None, "missing": lambda: None},
    )


def load_grade_payload(db: Session, assertion_id: uuid.UUID) -> dict[str, Any] | None:
    """Load one assertion's payload, or None if it's gone."""
    import json

    from sqlalchemy import text

    row = db.execute(
        text("SELECT payload FROM intel.assertion WHERE id = :id"),
        {"id": assertion_id},
    ).first()

    def _payload() -> dict[str, Any]:
        return pick(isinstance(row[0], dict), lambda: row[0], lambda: json.loads(row[0]))

    return apply(
        evaluate_presence(row).action,
        {"ok": _payload, "empty": lambda: None, "missing": lambda: None},
    )


def _normalized_correct_indices(payload: dict[str, Any]) -> list[int] | None:
    ci = payload.get("correct_indices")
    return pick(
        isinstance(ci, list) and len(ci) >= 2,
        lambda: [int(i) for i in ci],
        lambda: None,
    )


def grade_verdict(
    payload: dict[str, Any], choice_index: int, choice_indices: list[int] | None = None
) -> dict[str, Any]:
    """Instant, LLM-free verdict from the stored payload.

    This is everything the UI needs to reveal the outcome immediately: the LLM
    coaching (``grade_feedback``) is resolved separately and arrives after.
    """
    correct_index = int(payload.get("correct_index", 0))
    correct_indices = _normalized_correct_indices(payload)
    is_correct = evaluate_mcq_correct(
        is_multi=bool(correct_indices),
        choice_index=int(choice_index),
        correct_index=correct_index,
        choice_indices=choice_indices or [],
        correct_indices=correct_indices or [],
    )
    out: dict[str, Any] = {
        "correct": bool(is_correct),
        "correct_index": correct_index,
        "explanation": sanitize_mcq_explanation(payload.get("explanation") or ""),
    }
    pick(
        bool(correct_indices),
        lambda: out.__setitem__("correct_indices", sorted(correct_indices or [])),
        lambda: None,
    )
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
    option_feedback = pick(
        isinstance(option_feedback, dict), lambda: option_feedback, lambda: None
    )

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
    # instantly. Best-effort: a failed warm-up never affects this grade.
    pick(
        result.get("feedback_source") == "llm"
        and not correct_indices
        and 0 <= int(choice_index) < len(options),
        lambda: _persist_option_feedback(
            db, assertion_id, int(choice_index), str(result.get("feedback") or "")
        ),
        lambda: None,
    )
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

    def _missing() -> dict[str, Any]:
        return {"correct": False, "feedback": "Question not found", "correct_index": 0}

    def _grade() -> dict[str, Any]:
        out = grade_verdict(payload, choice_index, choice_indices)
        out.pop("explanation", None)  # JSON grade response never carried this field
        out["feedback"] = asyncio.run(
            grade_feedback(
                db,
                assertion_id,
                payload,
                choice_index=choice_index,
                choice_indices=choice_indices,
            )
        )
        return out

    return apply(
        evaluate_presence(payload).action,
        {"missing": _missing, "empty": _missing, "ok": _grade},
    )
