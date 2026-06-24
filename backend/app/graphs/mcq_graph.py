"""LangGraph MCQ grading — check answer → explain (when wrong)."""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.services.llm_router import complete_chat
from app.services.prompts import get_prompt
from app.services.token_budget import GRADE_CONTEXT_MAX_TOKENS, truncate_to_tokens

_LEARN_FEEDBACK_MAX = 280

_FORMAL_META_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^(?:(?:according|based)\s+to\s+(?:the\s+)?(?:page|text|passage|source|excerpt)"
        r"|from\s+(?:the\s+)?(?:page|text|passage|source)"
        r"|in\s+(?:the\s+)?(?:passage|text|excerpt)"
        r"|(?:the\s+)?(?:page|text|passage|source)\s+(?:text\s+)?"
        r"(?:specifies|states|says|indicates|describes|explains|mentions)(?:\s+that)?)[,:]?\s+",
        re.IGNORECASE,
    ),
    re.compile(r"^as\s+(?:the\s+)?(?:page|text|passage)\s+(?:states|says)[,:]?\s+", re.IGNORECASE),
    re.compile(r"^the\s+text\s+states:\s*['\"]?", re.IGNORECASE),
)

_FORMAL_RESIDUE_RE = re.compile(
    r"\b(?:page\s+text|the\s+text\s+states|according\s+to\s+the|passage\s+states|text\s+specifies|document\s+says)\b",
    re.IGNORECASE,
)


def _strip_formal_meta(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return ""
    for _ in range(6):
        changed = False
        for pattern in _FORMAL_META_PATTERNS:
            updated = pattern.sub("", cleaned).strip()
            if updated != cleaned:
                cleaned = updated
                changed = True
        if not changed:
            break
    cleaned = re.sub(r"\s*The text states:.*$", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned


def _learnify_explanation(text: str) -> str:
    """Turn stored explanations into short, memorable teacher voice."""
    cleaned = _strip_formal_meta(text)
    if not cleaned:
        return ""

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
    summary = " ".join(sentences[:2])
    if len(summary) > _LEARN_FEEDBACK_MAX:
        summary = summary[: _LEARN_FEEDBACK_MAX - 1].rstrip() + "…"
    if summary and summary[0].islower():
        summary = summary[0].upper() + summary[1:]
    return summary


def _has_formal_residue(text: str) -> bool:
    return bool(_FORMAL_RESIDUE_RE.search(text or ""))


def _wrong_feedback(*, chosen: str, correct: str, explanation: str) -> str:
    takeaway = _learnify_explanation(explanation)
    if takeaway:
        return (
            f"The answer is {correct}. {takeaway}\n\n"
            f"You chose {chosen} — close, but that misses the main point here."
        )
    return f"The answer is {correct}. You chose {chosen} — look for the option that matches that idea."


def _correct_feedback(explanation: str) -> str:
    return _learnify_explanation(explanation)


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
    fast = try_grade_mcq_fast(
        options=state.get("options") or [],
        correct_index=int(state["correct_index"]),
        selected_index=int(state["selected_index"]),
        explanation=(state.get("explanation") or ""),
    )
    if fast is not None:
        return {
            "is_correct": bool(fast["is_correct"]),
            "feedback": (fast.get("feedback") or "").strip(),
            "feedback_ready": True,
        }
    return {
        "is_correct": state["selected_index"] == state["correct_index"],
        "feedback_ready": False,
    }


def try_grade_mcq_fast(
    *,
    options: list[str],
    correct_index: int,
    selected_index: int,
    explanation: str = "",
) -> dict[str, Any] | None:
    """Return grading result without retrieval/LLM when the explanation is already learner-ready."""
    exp = (explanation or "").strip()
    correct = options[correct_index] if correct_index < len(options) else ""
    chosen = options[selected_index] if selected_index < len(options) else ""

    if selected_index == correct_index:
        plain = _correct_feedback(exp)
        if plain and not _has_formal_residue(plain):
            return {"is_correct": True, "feedback": plain}
        if not exp:
            return {"is_correct": True, "feedback": ""}
        return None

    if not exp:
        return None

    plain = _learnify_explanation(exp)
    if plain and not _has_formal_residue(plain):
        return {
            "is_correct": False,
            "feedback": _wrong_feedback(chosen=chosen, correct=correct, explanation=exp),
        }
    return None


async def _teach_feedback(state: McqGradeState, *, db: Session) -> dict[str, Any]:
    options = state.get("options") or []
    correct = options[state["correct_index"]] if state["correct_index"] < len(options) else ""
    chosen = options[state["selected_index"]] if state["selected_index"] < len(options) else ""
    is_correct = bool(state.get("is_correct"))
    system = get_prompt(db, "mcq_grader_system")
    outcome = "The learner answered correctly." if is_correct else "The learner answered incorrectly."
    user = (
        f"Question: {state.get('question', '')}\n"
        f"Options:\n"
        + "\n".join(f"{i}. {opt}" for i, opt in enumerate(options))
        + f"\n{outcome}\n"
        f"Student chose: {chosen}\n"
        f"Correct answer: {correct}\n"
        f"Author explanation: {(state.get('explanation') or '').strip() or '(none)'}\n"
    )
    if state.get("document_context"):
        user += f"\n(See document context above.)\n"
    user += "\nWrite teacher feedback only — no labels like 'Feedback:' or markdown."

    messages = [{"role": "system", "content": system}]
    doc_ctx = (state.get("document_context") or "").strip()
    if doc_ctx:
        messages.append(
            {
                "role": "user",
                "content": f"Document context (stable across grading on this page):\n{truncate_to_tokens(doc_ctx, GRADE_CONTEXT_MAX_TOKENS)}",
            }
        )
    messages.append({"role": "user", "content": user})

    feedback = await complete_chat(
        messages,
        db,
        log_tag="grade_mcq",
    )
    return {"feedback": _learnify_explanation(feedback.strip()) or feedback.strip()}


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
            "feedback": (fast.get("feedback") or "").strip(),
        }

    graph = get_mcq_grade_graph()
    state: McqGradeState = {
        "question": question,
        "options": options,
        "correct_index": correct_index,
        "selected_index": selected_index,
        "explanation": explanation,
        "document_context": document_context,
        "is_correct": selected_index == correct_index,
    }
    result = await graph.ainvoke(state, config={"configurable": {"db": db}})
    is_correct = selected_index == correct_index
    feedback = (result.get("feedback") or "").strip()
    if not feedback and is_correct:
        feedback = ""
    elif not feedback:
        feedback = "Let's look at this again — the right answer fits the idea we were testing."
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
        )
    )
    return {
        "correct": bool(result.get("is_correct")),
        "feedback": result.get("feedback"),
        "correct_index": correct_index,
    }
