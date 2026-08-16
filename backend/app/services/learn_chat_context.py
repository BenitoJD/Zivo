"""Authoritative Learn-mode context for tutor chat."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import apply, choose, pick
from app.models import Document
from app.services.question_pool import (
    assertion_page_number,
    build_learn_queue_state,
    get_progress,
    learner_key_for,
    page_range_bounds,
    selected_page_list,
)
from app.services.tutor_retrieval import (
    evaluate_learn_chat_status,
    label_confirmed_answer_outcome,
    plan_learn_context_rag,
    plan_unconfirmed_tutor_policy,
    should_attach_learn_session_context,
)


def _strip_opt(raw: Any) -> str:
    return str(raw).strip()


def _options_from_raw(raw_options: Any) -> list[str]:
    return apply(
        choose(isinstance(raw_options, list), "list", choose(isinstance(raw_options, dict), "dict", "empty")),
        {
            "list": lambda: list(filter(None, map(_strip_opt, raw_options))),
            "dict": lambda: list(filter(None, map(_strip_opt, raw_options.values()))),
            "empty": lambda: [],
        },
    )


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
    return pick(not row, lambda: None, lambda: _shape_mcq(row))


def _shape_mcq(row: Any) -> dict[str, Any]:
    stem = (row.get("question") or row.get("stem") or "").strip()
    raw_options = row.get("options") or row.get("choices")
    correct_index = row.get("correct_index")
    return {
        "stem": stem or None,
        "options": _options_from_raw(raw_options),
        "correct_index": pick(correct_index is not None, lambda: int(correct_index), lambda: None),
    }


def format_active_question_retrieval_chunk(
    db: Session,
    assertion_id: str,
    *,
    page: int | None = None,
) -> str | None:
    """Text block pinned at the top of tutor retrieval for the on-screen MCQ."""
    mcq = _assertion_mcq(db, assertion_id)
    return pick(not mcq or not mcq.get("stem"), lambda: None, lambda: _format_active(mcq, page))


def _format_active(mcq: dict[str, Any], page: int | None) -> str:
    lines = ["Active quiz question (authoritative — the learner is answering this now):"]
    pick(
        page is not None and page >= 1,
        lambda: lines.append(f"Source page: {page}"),
        lambda: None,
    )
    lines.append(f"Stem: {mcq['stem']}")
    for i, opt in enumerate(mcq.get("options") or []):
        lines.append(f"  {_choice_letter(i)}. {opt}")
    lines.append(
        "When the learner asks about a phrase from this stem, explain that phrase "
        "as part of this question — do not claim it is missing from the session."
    )
    return "\n".join(lines)


def _choice_letter(index: int) -> str:
    return chr(65 + max(0, index))


def _correct_flag(value: Any) -> bool | None:
    return pick(value is not None, lambda: bool(value), lambda: None)


def _confirmed_answer(
    assertion_id: str,
    *,
    scope: dict[str, Any] | None,
    progress: dict[str, Any],
) -> tuple[int | None, bool | None, list[int] | None]:
    """Resolve the learner's confirmed choice for the active question.

    Returns (first_index, correct, all_indices). all_indices is set for multi-select.
    """
    scoped = pick(scope is not None, lambda: _from_scope(scope, assertion_id), lambda: None)
    return pick(scoped is not None, lambda: scoped, lambda: _from_progress(progress, assertion_id))


def _from_scope(
    scope: dict[str, Any], assertion_id: str
) -> tuple[int | None, bool | None, list[int] | None] | None:
    scope_assertion = scope.get("current_assertion_id")
    return pick(
        scope_assertion is None or str(scope_assertion) == assertion_id,
        lambda: _from_scope_multi(scope),
        lambda: None,
    )


def _from_scope_multi(
    scope: dict[str, Any],
) -> tuple[int | None, bool | None, list[int] | None] | None:
    multi = scope.get("confirmed_choice_indices")
    return pick(
        isinstance(multi, list) and len(multi) >= 1,
        lambda: _multi_result(scope, multi),
        lambda: _from_scope_single(scope),
    )


def _multi_result(
    scope: dict[str, Any], multi: list[Any]
) -> tuple[int, bool | None, list[int]]:
    indices = [int(i) for i in multi]
    return indices[0], _correct_flag(scope.get("answer_correct")), indices


def _from_scope_single(
    scope: dict[str, Any],
) -> tuple[int | None, bool | None, list[int] | None] | None:
    return pick(
        scope.get("confirmed_choice_index") is not None,
        lambda: (
            int(scope["confirmed_choice_index"]),
            _correct_flag(scope.get("answer_correct")),
            None,
        ),
        lambda: None,
    )


def _from_progress(
    progress: dict[str, Any], assertion_id: str
) -> tuple[int | None, bool | None, list[int] | None]:
    last = progress.get("last_confirmed_answer") or {}
    return pick(
        str(last.get("assertion_id")) == assertion_id and last.get("choice_index") is not None,
        lambda: _progress_result(last),
        lambda: (None, None, None),
    )


def _progress_result(last: dict[str, Any]) -> tuple[int, bool | None, list[int] | None]:
    multi = last.get("choice_indices")
    indices = pick(
        isinstance(multi, list) and bool(multi),
        lambda: [int(i) for i in multi],
        lambda: None,
    )
    return int(last["choice_index"]), _correct_flag(last.get("correct")), indices


def _format_choice_labels(options: list[str], indices: list[int]) -> str:
    def _one(i: int) -> str:
        return pick(
            0 <= i < len(options),
            lambda: f'{_choice_letter(i)} ("{options[i]}")',
            lambda: _choice_letter(i),
        )

    parts = [_one(i) for i in indices]
    return choose(bool(parts), ", ".join(parts), "(none)")


def _newspaper_header(doc: Document) -> str | None:
    meta = doc.meta or {}
    title = str(meta.get("paper_title") or "").strip()
    edition = str(meta.get("edition_date") or "").strip()
    return pick(
        not title and not edition,
        lambda: None,
        lambda: pick(bool(title) and bool(edition), lambda: f"{title} · {edition}", lambda: title or edition),
    )


def _active_assertion_id(scope: dict[str, Any] | None, state: dict[str, Any]) -> str | None:
    return pick(
        bool(scope and scope.get("current_assertion_id")),
        lambda: str(scope["current_assertion_id"]),
        lambda: pick(bool(state.get("current_assertion_id")), lambda: str(state["current_assertion_id"]), lambda: None),
    )


def _question_source_page(
    db: Session, assertion_id: str | None, fallback: int
) -> int:
    def from_assertion() -> int:
        page = assertion_page_number(db, assertion_id)
        return pick(isinstance(page, int) and page >= 1, lambda: page, lambda: max(1, int(fallback)))

    return pick(bool(assertion_id), from_assertion, lambda: max(1, int(fallback)))


def build_learn_chat_context(
    db: Session,
    document_id: uuid.UUID,
    doc: Document,
    *,
    scope: dict[str, Any] | None = None,
    user: Any | None = None,
    guest_id: str | None = None,
) -> str | None:
    """Return a short authoritative block for the tutor when Learn mode is active."""
    scope_mode = pick(bool(scope), lambda: str(scope.get("mode") or ""), lambda: None)
    return pick(
        not should_attach_learn_session_context(scope_mode=scope_mode),
        lambda: None,
        lambda: _build_attached(db, document_id, doc, scope=scope, user=user, guest_id=guest_id),
    )


def _build_attached(
    db: Session,
    document_id: uuid.UUID,
    doc: Document,
    *,
    scope: dict[str, Any] | None,
    user: Any | None,
    guest_id: str | None,
) -> str | None:
    meta = doc.meta or {}
    return pick(
        not meta.get("question_pool_initialized"),
        lambda: None,
        lambda: _build_initialized(db, document_id, doc, scope=scope, user=user, guest_id=guest_id),
    )


def _build_initialized(
    db: Session,
    document_id: uuid.UUID,
    doc: Document,
    *,
    scope: dict[str, Any] | None,
    user: Any | None,
    guest_id: str | None,
) -> str:
    lk = learner_key_for(user, guest_id)
    progress = get_progress(doc, learner_key=lk)
    state = build_learn_queue_state(db, document_id, doc, progress, learner_key=lk)
    from app.services.newspaper import is_newspaper_document

    newspaper = is_newspaper_document(doc)
    page_from, page_to = page_range_bounds(doc)
    assertion_id = _active_assertion_id(scope, state)
    page = int(state["current_page"])
    budget = int(state["question_budget"])
    answered = int(state["questions_answered"])
    generated = int(state["questions_generated"])
    qnum = int(state["question_number"])

    rag_plan = plan_learn_context_rag(page, selected_page_list(doc), newspaper=newspaper)
    supplementary = list(filter(lambda p: p != page, rag_plan.pages))

    lines = ["Learn session (authoritative — use this for progress/position questions):"]
    apply(
        choose(newspaper, "newspaper", "upload"),
        {
            "newspaper": lambda: _append_newspaper_header(
                lines, doc, page, page_from, page_to, generated, answered, assertion_id, state
            ),
            "upload": lambda: _append_upload_header(lines, page, page_from, page_to),
        },
    )
    pick(
        bool(supplementary),
        lambda: lines.append(
            f"- Page {page} is the primary focus (active question). "
            f"Document excerpts from page(s) {', '.join(str(p) for p in supplementary)} "
            f"are supplementary — use them only "
            f"to deepen understanding of the topic on page {page}, not as the main subject."
        ),
        lambda: lines.append(f"- Page {page} is the primary focus for the active question."),
    )

    status = evaluate_learn_chat_status(
        document_complete=bool(state.get("document_complete")),
        page_complete=bool(state.get("page_complete")),
        generation_pending=bool(state.get("generation_pending")),
        current_assertion_id=state.get("current_assertion_id"),
        assertion_id=assertion_id,
    )
    return apply(
        status,
        {
            "document_complete": lambda: _finish(
                lines, "- Document study complete for the selected page range."
            ),
            "page_complete": lambda: _finish(
                lines, f"- Page {page} complete ({answered} of {budget} questions answered)."
            ),
            "generation_pending": lambda: _finish(
                lines,
                f"- Waiting for questions to generate on page {page} "
                f"({generated} written so far, target {budget}).",
            ),
            "active_question": lambda: _append_active_and_weak(
                db,
                lines,
                state=state,
                progress=progress,
                scope=scope,
                newspaper=newspaper,
                assertion_id=assertion_id,
                page=page,
                budget=budget,
                answered=answered,
                generated=generated,
                qnum=qnum,
            ),
            "idle": lambda: _append_idle_and_weak(lines, progress, page),
        },
    )


def _append_newspaper_header(
    lines: list[str],
    doc: Document,
    page: int,
    page_from: int,
    page_to: int,
    generated: int,
    answered: int,
    assertion_id: str | None,
    state: dict[str, Any],
) -> None:
    header = _newspaper_header(doc)
    pick(
        bool(header),
        lambda: lines.append(f"- Newspaper edition: {header} (pages {page_from}–{page_to})."),
        lambda: lines.append(f"- Newspaper edition: pages {page_from}–{page_to}."),
    )
    lines.append(
        f"- Studying page {page} now ({generated} questions in this edition; "
        f"{answered} answered edition-wide so far)."
    )
    page_total = int(state.get("edition_page_question_total") or 0)
    page_done = int(state.get("edition_page_questions_answered") or 0)
    pick(
        page_total > 0,
        lambda: lines.append(
            f"- On page {page}: question {page_done + choose(bool(assertion_id), 1, 0)} "
            f"of {page_total} for this page."
        ),
        lambda: None,
    )
    pick(
        bool(assertion_id),
        lambda: lines.append(f"- Ground tutor answers in page {page} of this edition."),
        lambda: None,
    )
    lines.append(
        "- The current question stem and options (below) are part of this session's "
        "material. If the learner asks about a term that appears in the stem, treat "
        "it as in scope — use the stem, the source page excerpts, and clear teaching "
        "language. Do not say it is absent just because a retrieved excerpt omitted it."
    )


def _append_upload_header(lines: list[str], page: int, page_from: int, page_to: int) -> None:
    lines.append(f"- Study range: pages {page_from}–{page_to}; currently on page {page}")
    lines.append(
        "- The current question stem and options (when listed below) are part of this "
        "session's material. Explain terms that appear in the stem using the stem, "
        "page excerpts, and teaching language — not by claiming they are missing."
    )


def _finish(lines: list[str], extra: str) -> str:
    lines.append(extra)
    return "\n".join(lines)


def _append_idle_and_weak(lines: list[str], progress: dict[str, Any], page: int) -> str:
    lines.append(f"- On page {page}; no active question right now.")
    _append_weak_concepts(lines, progress)
    return "\n".join(lines)


def _append_active_and_weak(
    db: Session,
    lines: list[str],
    *,
    state: dict[str, Any],
    progress: dict[str, Any],
    scope: dict[str, Any] | None,
    newspaper: bool,
    assertion_id: str | None,
    page: int,
    budget: int,
    answered: int,
    generated: int,
    qnum: int,
) -> str:
    aid = assertion_id or str(state["current_assertion_id"])
    apply(
        choose(newspaper, "newspaper", "upload"),
        {
            "newspaper": lambda: _append_newspaper_question(lines, state, page, answered),
            "upload": lambda: _append_upload_question(lines, qnum, budget, page, answered, generated),
        },
    )
    mcq = _assertion_mcq(db, aid)
    pick(
        bool(mcq and mcq.get("stem")),
        lambda: lines.append(f'- Current question stem: "{mcq["stem"]}"'),
        lambda: None,
    )
    pick(
        bool(mcq and mcq.get("options")),
        lambda: [lines.append(f"  {_choice_letter(i)}. {opt}") for i, opt in enumerate(mcq["options"])],
        lambda: None,
    )
    choice_index, answer_correct, choice_indices = _confirmed_answer(
        aid, scope=scope, progress=progress
    )
    pick(
        bool(mcq and mcq.get("options") and choice_index is None),
        lambda: _append_unconfirmed(lines, mcq, scope, choice_index),
        lambda: None,
    )
    pick(
        choice_index is not None and bool(mcq and mcq.get("options")),
        lambda: _append_confirmed(lines, mcq["options"], choice_index, answer_correct, choice_indices),
        lambda: None,
    )
    _append_weak_concepts(lines, progress)
    return "\n".join(lines)


def _append_newspaper_question(
    lines: list[str], state: dict[str, Any], page: int, answered: int
) -> None:
    page_total = int(state.get("edition_page_question_total") or 0)
    page_done = int(state.get("edition_page_questions_answered") or 0)
    pick(
        page_total > 0,
        lambda: lines.append(f"- Question {page_done + 1} of {page_total} on page {page}"),
        lambda: lines.append(f"- On page {page} of this edition"),
    )
    lines.append(f"- Answered edition-wide so far: {answered}")
    lines.append(f"- Source page for this question: {page}")


def _append_upload_question(
    lines: list[str], qnum: int, budget: int, page: int, answered: int, generated: int
) -> None:
    lines.append(f"- Question {qnum} of {budget} on page {page}")
    lines.append(f"- Answered on this page so far: {answered}")
    lines.append(f"- Questions generated on this page: {generated}")


def _append_unconfirmed(
    lines: list[str],
    mcq: dict[str, Any],
    scope: dict[str, Any] | None,
    choice_index: int | None,
) -> None:
    options = mcq.get("options") or []
    multi_raw = (scope or {}).get("selected_choice_indices")
    pick(
        isinstance(multi_raw, list) and len(multi_raw) >= 1,
        lambda: _append_multi_lean(lines, options, multi_raw),
        lambda: _append_single_lean(lines, options, scope),
    )
    hint_policy = plan_unconfirmed_tutor_policy(
        has_mcq_options=True,
        confirmed_choice_index=choice_index,
    )
    pick(bool(hint_policy), lambda: lines.append(hint_policy), lambda: None)


def _append_multi_lean(lines: list[str], options: list[str], multi_raw: list[Any]) -> None:
    sels = [int(i) for i in filter(lambda i: 0 <= int(i) < len(options), multi_raw)]
    pick(
        bool(sels),
        lambda: lines.append(
            f"- Learner is currently leaning toward: {_format_choice_labels(options, sels)} "
            f"but has NOT checked yet."
        ),
        lambda: None,
    )


def _append_single_lean(
    lines: list[str], options: list[str], scope: dict[str, Any] | None
) -> None:
    selected_raw = (scope or {}).get("selected_choice_index")
    pick(
        selected_raw is not None and 0 <= int(selected_raw) < len(options),
        lambda: _append_single_lean_line(lines, options, int(selected_raw)),
        lambda: None,
    )


def _append_single_lean_line(lines: list[str], options: list[str], sel: int) -> None:
    lines.append(
        f'- Learner is currently leaning toward option {_choice_letter(sel)} '
        f'("{options[sel]}") but has NOT checked it yet.'
    )


def _append_confirmed(
    lines: list[str],
    options: list[str],
    choice_index: int,
    answer_correct: bool | None,
    choice_indices: list[int] | None,
) -> None:
    outcome = label_confirmed_answer_outcome(answer_correct)
    pick(
        bool(choice_indices) and len(choice_indices) >= 2,
        lambda: lines.append(
            f"- Learner confirmed answers: {_format_choice_labels(options, choice_indices)} "
            f"— {outcome}"
        ),
        lambda: _append_single_confirmed(lines, options, choice_index, outcome),
    )


def _append_single_confirmed(
    lines: list[str], options: list[str], choice_index: int, outcome: str
) -> None:
    letter = _choice_letter(choice_index)
    selected_text = pick(
        0 <= choice_index < len(options),
        lambda: options[choice_index],
        lambda: "unknown",
    )
    lines.append(f"- Learner confirmed answer: {letter} ({selected_text}) — {outcome}")


def _append_weak_concepts(lines: list[str], progress: dict[str, Any]) -> None:
    concept_ability = progress.get("concept_ability") or {}
    pick(
        isinstance(concept_ability, dict) and bool(concept_ability),
        lambda: _append_weak_from_ability(lines, concept_ability),
        lambda: None,
    )


def _append_weak_from_ability(lines: list[str], concept_ability: dict[str, Any]) -> None:
    from app.services.tutor_retrieval import plan_tutor_weak_concepts

    weak = plan_tutor_weak_concepts(concept_ability)
    pick(
        bool(weak.concepts),
        lambda: lines.append(
            "- Learner's weakest concepts so far (lowest calibrated ability): "
            + "; ".join(f"{k} ({v:.2f})" for k, v in weak.concepts)
            + ". When teaching, prefer these concepts."
        ),
        lambda: None,
    )


def learn_scope_fields(
    db: Session,
    document_id: uuid.UUID,
    doc: Document,
    *,
    request_scope: dict[str, Any] | None = None,
    user: Any | None = None,
    guest_id: str | None = None,
) -> dict[str, int | str | bool | None]:
    """Fields to fold into chat scope (e.g. cache key) from live learn state."""
    meta = doc.meta or {}
    return pick(
        not meta.get("question_pool_initialized"),
        lambda: {},
        lambda: _learn_scope_initialized(
            db, document_id, doc, request_scope=request_scope, user=user, guest_id=guest_id
        ),
    )


def _learn_scope_initialized(
    db: Session,
    document_id: uuid.UUID,
    doc: Document,
    *,
    request_scope: dict[str, Any] | None,
    user: Any | None,
    guest_id: str | None,
) -> dict[str, int | str | bool | None]:
    lk = learner_key_for(user, guest_id)
    progress = get_progress(doc, learner_key=lk)
    state = build_learn_queue_state(db, document_id, doc, progress, learner_key=lk)
    req_aid = pick(
        bool(request_scope and request_scope.get("current_assertion_id")),
        lambda: str(request_scope["current_assertion_id"]),
        lambda: None,
    )
    assertion_id = req_aid or pick(
        bool(state.get("current_assertion_id")),
        lambda: str(state["current_assertion_id"]),
        lambda: None,
    )
    page = _question_source_page(db, assertion_id, int(state.get("current_page") or 1))
    fields: dict[str, int | str | bool | None] = {
        "question_number": int(state["question_number"]),
        "current_assertion_id": assertion_id,
        "current_page": page,
    }
    pick(bool(assertion_id), lambda: _fold_last_confirmed(fields, progress, assertion_id), lambda: None)
    pick(bool(request_scope), lambda: _fold_request_scope(fields, request_scope), lambda: None)
    return fields


def _fold_last_confirmed(
    fields: dict[str, int | str | bool | None],
    progress: dict[str, Any],
    assertion_id: str,
) -> None:
    last = progress.get("last_confirmed_answer") or {}
    pick(
        str(last.get("assertion_id")) == str(assertion_id),
        lambda: _apply_last(fields, last),
        lambda: None,
    )


def _apply_last(fields: dict[str, int | str | bool | None], last: dict[str, Any]) -> None:
    fields["confirmed_choice_index"] = last.get("choice_index")
    fields["answer_correct"] = last.get("correct")


def _fold_request_scope(
    fields: dict[str, int | str | bool | None], request_scope: dict[str, Any]
) -> None:
    pick(
        request_scope.get("confirmed_choice_index") is not None,
        lambda: fields.__setitem__("confirmed_choice_index", int(request_scope["confirmed_choice_index"])),
        lambda: None,
    )
    pick(
        request_scope.get("answer_correct") is not None,
        lambda: fields.__setitem__("answer_correct", bool(request_scope["answer_correct"])),
        lambda: None,
    )
    multi = request_scope.get("confirmed_choice_indices")
    pick(
        isinstance(multi, list) and bool(multi),
        lambda: fields.__setitem__("confirmed_choice_index", int(multi[0])),
        lambda: None,
    )
