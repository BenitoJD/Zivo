"""Authoritative Learn-mode context for tutor chat."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Document
from app.services.question_pool import (
    build_learn_queue_state,
    get_progress,
    page_range_bounds,
    selected_page_list,
)
from app.services.rag_window import chat_rag_window


def _assertion_mcq(db: Session, assertion_id: str) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT payload->>'question' AS question,
                   payload->>'stem' AS stem,
                   payload->'options' AS options,
                   payload->'choices' AS choices,
                   (payload->>'correct_index')::int AS correct_index
            FROM intel.assertion WHERE id = :id
            """
        ),
        {"id": assertion_id},
    ).mappings().first()
    if not row:
        return None
    stem = (row.get("question") or row.get("stem") or "").strip()
    raw_options = row.get("options") or row.get("choices")
    options: list[str] = []
    if isinstance(raw_options, list):
        options = [str(o).strip() for o in raw_options if str(o).strip()]
    elif isinstance(raw_options, dict):
        options = [str(v).strip() for v in raw_options.values() if str(v).strip()]
    correct_index = row.get("correct_index")
    return {
        "stem": stem or None,
        "options": options,
        "correct_index": int(correct_index) if correct_index is not None else None,
    }


def _choice_letter(index: int) -> str:
    return chr(65 + max(0, index))


def _confirmed_answer(
    assertion_id: str,
    *,
    scope: dict[str, Any] | None,
    progress: dict[str, Any],
) -> tuple[int | None, bool | None, list[int] | None]:
    """Resolve the learner's confirmed choice for the active question.

    Returns (first_index, correct, all_indices). all_indices is set for multi-select.
    """
    if scope is not None:
        scope_assertion = scope.get("current_assertion_id")
        if scope_assertion is None or str(scope_assertion) == assertion_id:
            multi = scope.get("confirmed_choice_indices")
            if isinstance(multi, list) and len(multi) >= 1:
                indices = [int(i) for i in multi]
                correct = scope.get("answer_correct")
                return indices[0], (bool(correct) if correct is not None else None), indices
            if scope.get("confirmed_choice_index") is not None:
                correct = scope.get("answer_correct")
                return int(scope["confirmed_choice_index"]), (
                    bool(correct) if correct is not None else None
                ), None
    last = progress.get("last_confirmed_answer") or {}
    if str(last.get("assertion_id")) == assertion_id and last.get("choice_index") is not None:
        correct = last.get("correct")
        multi = last.get("choice_indices")
        indices = [int(i) for i in multi] if isinstance(multi, list) and multi else None
        return int(last["choice_index"]), bool(correct) if correct is not None else None, indices
    return None, None, None


def _format_choice_labels(options: list[str], indices: list[int]) -> str:
    parts: list[str] = []
    for i in indices:
        if 0 <= i < len(options):
            parts.append(f'{_choice_letter(i)} ("{options[i]}")')
        else:
            parts.append(_choice_letter(i))
    return ", ".join(parts) if parts else "(none)"


def build_learn_chat_context(
    db: Session,
    document_id: uuid.UUID,
    doc: Document,
    *,
    scope: dict[str, Any] | None = None,
) -> str | None:
    """Return a short authoritative block for the tutor when Learn mode is active."""
    # Read-mode chat is about the document being read, not the Learn loop.
    if scope and str(scope.get("mode") or "").lower() == "read":
        return None
    meta = doc.meta or {}
    if not meta.get("question_pool_initialized"):
        return None

    progress = get_progress(doc)
    state = build_learn_queue_state(db, document_id, doc, progress)
    from app.services.newspaper import is_newspaper_document

    newspaper = is_newspaper_document(doc)
    page_from, page_to = page_range_bounds(doc)
    page = int(state["current_page"])
    budget = int(state["question_budget"])
    answered = int(state["questions_answered"])
    generated = int(state["questions_generated"])
    qnum = int(state["question_number"])

    rag_pages = chat_rag_window(page, selected_page_list(doc))
    supplementary = [p for p in rag_pages if p != page]

    lines = [
        "Learn session (authoritative — use this for progress/position questions):",
    ]
    if newspaper:
        lines.append(
            f"- Newspaper edition: pages {page_from}–{page_to}; "
            f"serving all pre-cooked questions ({generated} available)."
        )
        lines.append(
            f"- Active question {qnum} of {max(generated, 1)} answered so far ({answered} done)."
        )
    else:
        lines.append(f"- Study range: pages {page_from}–{page_to}; currently on page {page}")
    if supplementary:
        sup = ", ".join(str(p) for p in supplementary)
        lines.append(
            f"- Page {page} is the primary focus (active question). "
            f"Document excerpts from page(s) {sup} are supplementary — use them only "
            f"to deepen understanding of the topic on page {page}, not as the main subject."
        )
    else:
        lines.append(f"- Page {page} is the primary focus for the active question.")

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
        # Prefer the question the learner is actually looking at (sent in scope) so
        # the tutor is never confused about "the current question".
        assertion_id = str((scope or {}).get("current_assertion_id") or state["current_assertion_id"])
        if newspaper:
            lines.append(f"- Question {qnum} of {max(generated, 1)} in this edition")
            lines.append(f"- Answered in this edition so far: {answered}")
            lines.append(f"- Source page for this question: {page}")
        else:
            lines.append(f"- Question {qnum} of {budget} on page {page}")
            lines.append(f"- Answered on this page so far: {answered}")
            lines.append(f"- Questions generated on this page: {generated}")
        mcq = _assertion_mcq(db, assertion_id)
        if mcq and mcq.get("stem"):
            lines.append(f'- Current question stem: "{mcq["stem"]}"')
        if mcq and mcq.get("options"):
            for i, opt in enumerate(mcq["options"]):
                lines.append(f'  {_choice_letter(i)}. {opt}')
        choice_index, answer_correct, choice_indices = _confirmed_answer(
            assertion_id,
            scope=scope,
            progress=progress,
        )
        if mcq and mcq.get("options") and choice_index is None:
            # The learner may have picked an option but not checked it yet — tell the
            # tutor so it's fully aware of where they are.
            options = mcq.get("options") or []
            multi_raw = (scope or {}).get("selected_choice_indices")
            if isinstance(multi_raw, list) and len(multi_raw) >= 1:
                sels = [int(i) for i in multi_raw if 0 <= int(i) < len(options)]
                if sels:
                    lines.append(
                        f"- Learner is currently leaning toward: {_format_choice_labels(options, sels)} "
                        f"but has NOT checked yet."
                    )
            else:
                selected_raw = (scope or {}).get("selected_choice_index")
                if selected_raw is not None and 0 <= int(selected_raw) < len(options):
                    sel = int(selected_raw)
                    lines.append(
                        f'- Learner is currently leaning toward option {_choice_letter(sel)} '
                        f'("{options[sel]}") but has NOT checked it yet.'
                    )
            lines.append(
                "- Tutor policy: explain concepts and give hints only; do not reveal "
                "which option is correct unless the learner explicitly asks for the answer."
            )
        if choice_index is not None and mcq and mcq.get("options"):
            options = mcq["options"]
            outcome = (
                "correct"
                if answer_correct is True
                else "incorrect"
                if answer_correct is False
                else "submitted"
            )
            if choice_indices and len(choice_indices) >= 2:
                lines.append(
                    f"- Learner confirmed answers: {_format_choice_labels(options, choice_indices)} "
                    f"— {outcome}"
                )
            else:
                letter = _choice_letter(choice_index)
                selected_text = (
                    options[choice_index]
                    if 0 <= choice_index < len(options)
                    else "unknown"
                )
                lines.append(
                    f"- Learner confirmed answer: {letter} ({selected_text}) — {outcome}"
                )
    else:
        lines.append(f"- On page {page}; no active question right now.")

    return "\n".join(lines)


def learn_scope_fields(
    db: Session,
    document_id: uuid.UUID,
    doc: Document,
    *,
    request_scope: dict[str, Any] | None = None,
) -> dict[str, int | str | bool | None]:
    """Fields to fold into chat scope (e.g. cache key) from live learn state."""
    meta = doc.meta or {}
    if not meta.get("question_pool_initialized"):
        return {}
    progress = get_progress(doc)
    state = build_learn_queue_state(db, document_id, doc, progress)
    assertion_id = state.get("current_assertion_id")
    fields: dict[str, int | str | bool | None] = {
        "question_number": int(state["question_number"]),
        "current_assertion_id": str(assertion_id) if assertion_id is not None else None,
    }
    if assertion_id:
        last = progress.get("last_confirmed_answer") or {}
        if str(last.get("assertion_id")) == str(assertion_id):
            fields["confirmed_choice_index"] = last.get("choice_index")
            fields["answer_correct"] = last.get("correct")
    if request_scope:
        if request_scope.get("confirmed_choice_index") is not None:
            fields["confirmed_choice_index"] = int(request_scope["confirmed_choice_index"])
        if request_scope.get("answer_correct") is not None:
            fields["answer_correct"] = bool(request_scope["answer_correct"])
        multi = request_scope.get("confirmed_choice_indices")
        if isinstance(multi, list) and multi:
            # Cache-key / scope fold: keep first index for legacy fields; full set
            # is available on request_scope for learn_chat_context.
            fields["confirmed_choice_index"] = int(multi[0])
    return fields
