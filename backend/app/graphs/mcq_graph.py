"""LangGraph MCQ grading — check answer → teach (LLM feedback for every answer)."""

from __future__ import annotations

import uuid
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.services.llm_router import complete_chat
from app.services.mcq_dedup import sanitize_mcq_explanation
from app.services.prompts import get_prompt
from app.services.token_budget import GRADE_CONTEXT_MAX_TOKENS, truncate_to_tokens


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


async def _teach_feedback(state: McqGradeState, *, db: Session) -> dict[str, Any]:
    options = state.get("options") or []
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
        f"Learner chose: {chosen}",
        f"Correct answer: {correct}",
    ]
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
) -> dict[str, Any]:
    is_correct = int(selected_index) == int(correct_index)

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
    result = await graph.ainvoke(state, config={"configurable": {"db": db}})
    feedback = (result.get("feedback") or "").strip()
    if not feedback:
        feedback = _fallback_feedback(
            is_correct=is_correct,
            correct=options[correct_index] if correct_index < len(options) else "",
            chosen=options[selected_index] if selected_index < len(options) else "",
        )
    return {
        "is_correct": is_correct,
        "feedback": feedback,
    }


def grade_mcq(db: Session, assertion_id: uuid.UUID, choice_index: int) -> dict[str, Any]:
    """Sync wrapper for API — loads assertion payload from intel."""
    import asyncio
    import json
    from sqlalchemy import text

    row = db.execute(
        text("SELECT payload FROM intel.assertion WHERE id = :id"),
        {"id": assertion_id},
    ).first()
    if not row:
        return {"correct": False, "feedback": "Question not found"}
    payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    options = payload.get("options") or payload.get("choices") or []
    correct_index = int(payload.get("correct_index", 0))
    result = asyncio.run(
        grade_mcq_answer(
            db,
            question=payload.get("question") or payload.get("stem") or "",
            options=options,
            correct_index=correct_index,
            selected_index=choice_index,
            explanation=payload.get("explanation") or "",
            aspect_label=payload.get("primary_concept") or payload.get("primary_concept_key") or "",
        )
    )
    return {
        "correct": bool(result.get("is_correct")),
        "feedback": result.get("feedback"),
        "correct_index": correct_index,
    }
