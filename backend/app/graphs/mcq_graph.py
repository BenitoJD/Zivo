"""LangGraph MCQ grading — check answer → explain (when wrong)."""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.services.llm_router import complete_chat
from app.services.prompts import get_prompt

_LEARN_FEEDBACK_MAX = 300


def _learnify_explanation(text: str) -> str:
    """Trim and simplify stored explanations for learner-facing feedback."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return ""

    cleaned = re.sub(
        r"\s*The text states:\s*['\"].*?['\"]\s*",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s*The text states:.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip()

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
    summary = " ".join(sentences[:2])
    if len(summary) > _LEARN_FEEDBACK_MAX:
        summary = summary[: _LEARN_FEEDBACK_MAX - 1].rstrip() + "…"
    return summary


def _wrong_feedback(*, chosen: str, correct: str, explanation: str) -> str:
    plain = _learnify_explanation(explanation)
    intro = f'The answer is "{correct}". You picked "{chosen}".'
    if plain:
        return f"{intro}\n\n{plain}"
    return intro


def _correct_feedback(explanation: str) -> str:
    plain = _learnify_explanation(explanation)
    return plain or "Nice work — you got it."


class McqGradeState(TypedDict, total=False):
    question: str
    options: list[str]
    correct_index: int
    selected_index: int
    explanation: str
    document_context: str
    is_correct: bool
    feedback: str


def _check_answer(state: McqGradeState) -> dict[str, Any]:
    correct = state["selected_index"] == state["correct_index"]
    if correct:
        base = (state.get("explanation") or "").strip()
        feedback = _correct_feedback(base) if base else "Nice work — you got it."
        return {"is_correct": True, "feedback": feedback}
    return {"is_correct": False}


def try_grade_mcq_fast(
    *,
    options: list[str],
    correct_index: int,
    selected_index: int,
    explanation: str = "",
) -> dict[str, Any] | None:
    """Return grading result without retrieval/LLM when possible."""
    if selected_index == correct_index:
        base = (explanation or "").strip()
        return {
            "is_correct": True,
            "feedback": _correct_feedback(base),
        }

    exp = (explanation or "").strip()
    if not exp:
        return None

    correct = options[correct_index] if correct_index < len(options) else ""
    chosen = options[selected_index] if selected_index < len(options) else ""
    return {
        "is_correct": False,
        "feedback": _wrong_feedback(chosen=chosen, correct=correct, explanation=exp),
    }


async def _explain_wrong(state: McqGradeState, *, db: Session) -> dict[str, Any]:
    options = state.get("options") or []
    correct = options[state["correct_index"]] if state["correct_index"] < len(options) else ""
    chosen = options[state["selected_index"]] if state["selected_index"] < len(options) else ""
    system = get_prompt(db, "mcq_grader_system")
    user = (
        f"Question: {state.get('question', '')}\n"
        f"Options:\n"
        + "\n".join(f"{i}. {opt}" for i, opt in enumerate(options))
        + f"\nStudent chose: {chosen}\n"
        f"Correct answer: {correct}\n"
        f"Author explanation: {(state.get('explanation') or '').strip() or '(none)'}\n"
    )
    if state.get("document_context"):
        user += f"\nDocument context:\n{state['document_context'][:6000]}\n"
    user += "\nExplain in plain English for a learner. Short and friendly."

    feedback = await complete_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        db,
    )
    return {"feedback": feedback.strip()}


def build_mcq_grade_graph():
    graph = StateGraph(McqGradeState)

    async def explain_node(state: McqGradeState, config) -> dict[str, Any]:
        configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
        db: Session = configurable["db"]
        return await _explain_wrong(state, db=db)

    graph.add_node("check", _check_answer)
    graph.add_node("explain", explain_node)

    def route(state: McqGradeState) -> Literal["done", "explain"]:
        return "done" if state.get("is_correct") else "explain"

    graph.add_edge(START, "check")
    graph.add_conditional_edges("check", route, {"done": END, "explain": "explain"})
    graph.add_edge("explain", END)
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
    document_context: str = "",
) -> dict[str, Any]:
    fast = try_grade_mcq_fast(
        options=options,
        correct_index=correct_index,
        selected_index=selected_index,
        explanation=explanation,
    )
    if fast is not None:
        return {
            "is_correct": bool(fast["is_correct"]),
            "feedback": (fast.get("feedback") or "").strip() or "Review the explanation and try again.",
        }

    graph = get_mcq_grade_graph()
    state: McqGradeState = {
        "question": question,
        "options": options,
        "correct_index": correct_index,
        "selected_index": selected_index,
        "explanation": explanation,
        "document_context": document_context,
    }
    result = await graph.ainvoke(state, config={"configurable": {"db": db}})
    return {
        "is_correct": bool(result.get("is_correct")),
        "feedback": (result.get("feedback") or "").strip() or "Review the explanation and try again.",
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
        )
    )
    return {
        "correct": bool(result.get("is_correct")),
        "feedback": result.get("feedback"),
        "correct_index": correct_index,
    }
