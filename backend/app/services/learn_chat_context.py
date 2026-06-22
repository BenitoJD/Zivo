"""Authoritative Learn-mode context for tutor chat."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Document
from app.services.question_pool import (
    build_learn_queue_state,
    get_progress,
    page_range_bounds,
)


def _assertion_question(db: Session, assertion_id: str) -> str | None:
    row = db.execute(
        text("SELECT payload->>'question' AS question FROM intel.assertion WHERE id = :id"),
        {"id": assertion_id},
    ).mappings().first()
    if not row:
        return None
    question = (row.get("question") or "").strip()
    return question or None


def build_learn_chat_context(db: Session, document_id: uuid.UUID, doc: Document) -> str | None:
    """Return a short authoritative block for the tutor when Learn mode is active."""
    meta = doc.meta or {}
    if not meta.get("question_pool_initialized"):
        return None

    progress = get_progress(doc)
    state = build_learn_queue_state(db, document_id, doc, progress)
    page_from, page_to = page_range_bounds(doc)
    page = int(state["current_page"])
    budget = int(state["question_budget"])
    answered = int(state["questions_answered"])
    generated = int(state["questions_generated"])
    qnum = int(state["question_number"])

    lines = [
        "Learn session (authoritative — use this for progress/position questions):",
        f"- Study range: pages {page_from}–{page_to}; currently on page {page}",
    ]

    if state.get("document_complete"):
        lines.append("- Document study complete for the selected page range.")
        return "\n".join(lines)

    if state.get("page_complete") and not state.get("current_assertion_id"):
        lines.append(f"- Page {page} complete ({answered} of {budget} questions answered).")
        return "\n".join(lines)

    if state.get("generation_pending") and not state.get("current_assertion_id"):
        lines.append(
            f"- Waiting for questions to generate on page {page} "
            f"({generated} written so far, target {budget})."
        )
        return "\n".join(lines)

    if state.get("current_assertion_id"):
        lines.append(f"- Question {qnum} of {budget} on page {page}")
        lines.append(f"- Answered on this page so far: {answered}")
        lines.append(f"- Questions generated on this page: {generated}")
        stem = _assertion_question(db, str(state["current_assertion_id"]))
        if stem:
            lines.append(f'- Current question stem: "{stem}"')
    else:
        lines.append(f"- On page {page}; no active question right now.")

    return "\n".join(lines)


def learn_scope_fields(db: Session, document_id: uuid.UUID, doc: Document) -> dict[str, int | str | None]:
    """Fields to fold into chat scope (e.g. cache key) from live learn state."""
    meta = doc.meta or {}
    if not meta.get("question_pool_initialized"):
        return {}
    progress = get_progress(doc)
    state = build_learn_queue_state(db, document_id, doc, progress)
    return {
        "question_number": int(state["question_number"]),
        "current_assertion_id": state.get("current_assertion_id"),
    }
