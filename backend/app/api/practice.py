"""Public practice library — browse the Wikidata concept graph and practice MCQs.

No-auth: every read endpoint is open. On-demand generation requires a guest
session (httponly cookie) + CSRF-or-guest + rate limiting, to throttle abuse of
the LLM-backed generator. Anonymous answers are recorded against a cohort entity
(see mcq.py grade) to feed calibration.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.repositories.intel import (
    count_concept_questions,
    get_concept_entity_by_qid,
    get_concept_label,
    get_concept_questions,
    get_concept_relations,
)
from app.services import wikidata
from app.services.auth import require_csrf_or_guest
from app.services.guest_session import guest_session_for_read
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


def _ensure_practice_enabled() -> None:
    """Kill switch — flips the whole feature off via config."""
    if not get_settings().practice_enabled:
        raise HTTPException(status_code=404, detail="Practice library is disabled")


def _sanitize_payload(payload: dict | None) -> dict:
    """Strip private/keys the public UI should never see. Mirrors assertions.py."""
    if not isinstance(payload, dict):
        return {}
    out = dict(payload)
    # Drop internal/private keys.
    for key in ("artifact_id", "_assertion_id", "page_content_hash", "reused_from_shared"):
        out.pop(key, None)
    return out


def _run_async(coro):
    """Run an async coroutine from a sync endpoint."""
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False

    if in_loop:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
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
    if not qid.startswith("Q"):
        raise HTTPException(status_code=400, detail="Invalid QID")

    entity_id = get_concept_entity_by_qid(db, qid)
    count = count_concept_questions(db, entity_id) if entity_id else 0

    # Label + description from Wikidata (always fresh; cheap cached-ish call).
    label = get_concept_label(db, entity_id) if entity_id else qid
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
        # Network/lookup failure is non-fatal for a read; show what we have.
        pass

    # Children come from our own relation graph (materialized at generation time).
    if entity_id:
        for c in get_concept_relations(db, entity_id, direction="children"):
            children.append(
                ConceptSummary(qid=c.get("qid") or "", label=c.get("label") or "", description="")
            )

    # Only claim "generating" when a generate.questions job is actually queued/running
    # for this practice QID. Entity-with-zero-questions used to stick the UI forever
    # after a failed or abandoned generation.
    is_generating = False
    if entity_id is not None and count == 0:
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
        is_generating = active is not None

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
    # intentionally omitted here — grade via /api/mcq/grade.
    is_multi: bool = False


class QuestionsOut(BaseModel):
    qid: str
    question_count: int
    items: list[QuestionItem]


@router.get("/concepts/{qid}/questions", response_model=QuestionsOut)
def list_questions(
    qid: str,
    db: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
) -> QuestionsOut:
    """Active MCQs testing this concept. Omits the answer unless requested via grade."""
    _ensure_practice_enabled()
    qid = qid.strip().upper()
    entity_id = get_concept_entity_by_qid(db, qid)
    if not entity_id:
        return QuestionsOut(qid=qid, question_count=0, items=[])
    total = count_concept_questions(db, entity_id)
    rows = get_concept_questions(db, entity_id, limit=limit, offset=offset)
    items: list[QuestionItem] = []
    for row in rows:
        payload = _sanitize_payload(row.get("payload") if isinstance(row.get("payload"), dict) else {})
        raw_ci = payload.get("correct_indices")
        is_multi = isinstance(raw_ci, list) and len(raw_ci) >= 2
        items.append(
            QuestionItem(
                id=str(row["id"]),
                question=payload.get("question") or row.get("title") or "",
                options=list(payload.get("options") or []),
                is_multi=is_multi,
            )
        )
    return QuestionsOut(qid=qid, question_count=total, items=items)


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
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
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
