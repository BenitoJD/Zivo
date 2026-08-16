"""Public practice library — browse the Wikidata concept graph and practice MCQs.

No-auth: every read endpoint is open. On-demand generation requires a guest
session (httponly cookie) + CSRF-or-guest + rate limiting, to throttle abuse of
the LLM-backed generator. Anonymous answers are recorded per guest cookie or signed-in account
(see mcq.py grade) to feed calibration.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.engine_runtime import pick
from app.models import Account
from app.repositories.intel import (
    count_concept_questions,
    get_concept_entity_by_qid,
    get_concept_label,
    get_concept_questions,
    get_concept_relations,
)
from app.services import wikidata
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.answer_signal import answered_assertion_ids, resolve_subject_entity
from app.services.guest_session import guest_session_for_read, require_actor
from app.services.practice_generation import (
    DEFAULT_MIN_QUESTIONS,
    concept_status,
    ensure_concept_questions,
)
from app.services.rate_limit import rate_limit_dependency

router = APIRouter()


class ConceptSummary(BaseModel):
    qid: str
    label: str
    description: str


class SearchOut(BaseModel):
    query: str
    results: list[ConceptSummary]


def _raise_http(status: int, detail: str) -> None:
    raise HTTPException(status_code=status, detail=detail)


def _ensure_practice_enabled() -> None:
    """Kill switch: flips the whole feature off via config."""
    pick(
        get_settings().practice_enabled,
        lambda: None,
        lambda: _raise_http(404, "Practice library is disabled"),
    )


def _sanitize_payload(payload: dict | None) -> dict:
    """Strip private/keys the public UI should never see. Mirrors assertions.py."""
    def _strip() -> dict:
        out = dict(payload)
        for key in ("artifact_id", "_assertion_id", "page_content_hash", "reused_from_shared"):
            out.pop(key, None)
        return out

    return pick(isinstance(payload, dict), _strip, lambda: {})


def _run_async(coro):
    """Run an async coroutine from a sync endpoint."""
    import asyncio
    import concurrent.futures

    def _in_loop():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()

    try:
        asyncio.get_running_loop()
        return _in_loop()
    except RuntimeError:
        return asyncio.run(coro)


@router.get("/search")
def search(q: str = Query(..., min_length=1, max_length=200), limit: int = Query(10, ge=1, le=50)) -> SearchOut:
    """Search the Wikidata concept graph by free text."""
    _ensure_practice_enabled()
    try:
        hits = _run_async(wikidata.search_concepts(q, limit=limit))
    except wikidata.WikidataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return SearchOut(
        query=q,
        results=[
            ConceptSummary(qid=h.qid, label=h.label, description=h.description) for h in hits
        ],
    )


class ConceptOut(BaseModel):
    qid: str
    label: str
    description: str
    question_count: int
    is_generating: bool = False
    parents: list[ConceptSummary] = []
    children: list[ConceptSummary] = []


@router.get("/concepts/{qid}", response_model=ConceptOut)
def get_concept(qid: str, db: Session = Depends(get_db)) -> ConceptOut:
    """Concept detail: label, description (from Wikidata), question count, hierarchy."""
    _ensure_practice_enabled()
    qid = qid.strip().upper()
    pick(qid.startswith("Q"), lambda: None, lambda: _raise_http(400, "Invalid QID"))

    entity_id = get_concept_entity_by_qid(db, qid)
    count = pick(
        bool(entity_id),
        lambda: count_concept_questions(db, entity_id),
        lambda: 0,
    )

    label = pick(bool(entity_id), lambda: get_concept_label(db, entity_id), lambda: qid)
    description = ""
    parents: list[ConceptSummary] = []
    children: list[ConceptSummary] = []
    try:
        detail = _run_async(wikidata.get_concept(qid))
        label = detail.label or label
        description = detail.description
        for p in detail.parents:
            parents.append(ConceptSummary(qid=p.qid, label=p.label, description=p.description))
    except wikidata.WikidataError:
        pass

    def _load_children() -> None:
        for c in get_concept_relations(db, entity_id, direction="children"):
            children.append(
                ConceptSummary(qid=c.get("qid") or "", label=c.get("label") or "", description="")
            )

    pick(bool(entity_id), _load_children, lambda: None)

    def _check_generating() -> bool:
        from sqlalchemy import text as sa_text

        active = db.execute(
            sa_text(
                """
                SELECT 1 FROM qb.jobs
                WHERE name = 'generate.questions'
                  AND status IN ('queued', 'running')
                  AND (
                    payload->>'practice_qid' = :qid
                    OR payload->>'document_id' IN (
                      SELECT id::text FROM qb.documents
                      WHERE meta->>'wikidata_qid' = :qid
                    )
                  )
                LIMIT 1
                """
            ),
            {"qid": qid},
        ).scalar()
        return active is not None

    is_generating = pick(
        entity_id is not None and count == 0,
        _check_generating,
        lambda: False,
    )

    return ConceptOut(
        qid=qid,
        label=label,
        description=description,
        question_count=count,
        is_generating=is_generating,
        parents=parents,
        children=children,
    )


class QuestionItem(BaseModel):
    id: str
    question: str
    options: list[str] = []
    # True when the item is select-all-that-apply. Answers/explanations are
    # intentionally omitted here: grade via /api/mcq/grade.
    is_multi: bool = False


class QuestionsOut(BaseModel):
    qid: str
    question_count: int
    items: list[QuestionItem]
    answered_ids: list[str] = []
    resume_index: int = 0


@router.get("/concepts/{qid}/questions", response_model=QuestionsOut)
def list_questions(
    qid: str,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
) -> QuestionsOut:
    """Active MCQs testing this concept. Omits the answer unless requested via grade.

    Only includes assertions whose underlying document is publicly accessible
    (account_id NULL with is_public / is_demo, e.g. newspaper editions and demo
    docs) or owned by the signed-in caller. Questions from private documents
    must not appear here — they'd be listed but ungradeable (the grade endpoint
    requires document ownership), and would leak question stems from other
    users' private uploads.
    """
    _ensure_practice_enabled()
    qid = qid.strip().upper()
    entity_id = get_concept_entity_by_qid(db, qid)

    def _empty() -> QuestionsOut:
        return QuestionsOut(qid=qid, question_count=0, items=[])

    def _with_entity() -> QuestionsOut:
        owner_id = pick(user is not None, lambda: user.id, lambda: None)
        total = count_concept_questions(
            db, entity_id, public_only=True, owner_account_id=owner_id
        )
        rows = get_concept_questions(
            db, entity_id, limit=limit, offset=offset, public_only=True, owner_account_id=owner_id
        )
        items: list[QuestionItem] = []
        item_ids: list[str] = []
        for row in rows:
            payload = _sanitize_payload(row.get("payload"))
            raw_ci = payload.get("correct_indices")
            is_multi = isinstance(raw_ci, list) and len(raw_ci) >= 2
            item_id = str(row["id"])
            item_ids.append(item_id)
            items.append(
                QuestionItem(
                    id=item_id,
                    question=payload.get("question") or row.get("title") or "",
                    options=list(payload.get("options") or []),
                    is_multi=is_multi,
                )
            )

        answered_ids: list[str] = []
        resume_index = 0
        subject_entity_id = resolve_subject_entity(db, user, guest_id)

        def _fill_answered() -> None:
            nonlocal answered_ids, resume_index
            answered_set = answered_assertion_ids(db, subject_entity_id, item_ids)
            answered_ids = list(filter(lambda item_id: item_id in answered_set, item_ids))
            while resume_index < len(items) and items[resume_index].id in answered_set:
                resume_index += 1

        pick(bool(subject_entity_id) and bool(item_ids), _fill_answered, lambda: None)

        return QuestionsOut(
            qid=qid,
            question_count=total,
            items=items,
            answered_ids=answered_ids,
            resume_index=resume_index,
        )

    return pick(not entity_id, _empty, _with_entity)


class GenerateOut(BaseModel):
    status: str
    qid: str
    label: str = ""
    question_count: int
    job_id: str | None = None
    error: str | None = None


@router.post(
    "/concepts/{qid}/generate",
    response_model=GenerateOut,
    dependencies=[
        Depends(require_csrf_or_guest),
        Depends(rate_limit_dependency),
        Depends(require_actor),
    ],
)
def generate(
    qid: str,
    db: Session = Depends(get_db),
    min_count: int = Query(default=DEFAULT_MIN_QUESTIONS, ge=1, le=20),
    guest_id: str | None = Depends(guest_session_for_read),
) -> GenerateOut:
    """Ensure a concept has practice questions — fetches Wikipedia + enqueues generation."""
    _ensure_practice_enabled()
    outcome = ensure_concept_questions(db, qid.strip().upper(), min_count=min_count)
    return GenerateOut(
        status=outcome.status,
        qid=outcome.qid,
        label=outcome.label,
        question_count=outcome.question_count,
        job_id=outcome.job_id,
        error=outcome.error,
    )


@router.get("/concepts/{qid}/status", response_model=GenerateOut)
def generation_status(qid: str, db: Session = Depends(get_db)) -> GenerateOut:
    """Lightweight poll for question count without touching the network."""
    _ensure_practice_enabled()
    outcome = concept_status(db, qid.strip().upper())
    return GenerateOut(
        status=outcome.status,
        qid=outcome.qid,
        label=outcome.label,
        question_count=outcome.question_count,
    )
