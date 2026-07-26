"""Debug diagnostics — cook, browse, grade, and admin curate.

Surfaces:
- Cook: ``POST /api/debug/cook`` — learner/admin material intake
- Public bank: ``GET /api/debug`` — published scenarios
- Play: ``GET /api/debug/{id}``, ``POST /api/debug/{id}/grade-step``
- Admin: ``/api/debug/admin*`` — review queue, curate, publish
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.db import get_db
from app.models import Account
from app.services.answer_signal import resolve_subject_entity
from app.services.auth import get_current_user, get_optional_user, require_admin, require_csrf
from app.services.debug_cook import (
    enqueue_cook_from_material,
    get_cook_job,
    submit_job_to_library,
)
from app.services.debug_curation import (
    editorial_payload,
    seed_starter_bank,
    set_published,
    set_review_status,
    soft_delete_curated,
    upsert_curated_scenario,
    public_payload,
)
from app.services.debug_grading import grade_step, record_debug_understood
from app.services.guest_session import optional_guest_session
from app.services.llm_router import stream_chat_completion
from app.services.rate_limit import rate_limit_dependency

router = APIRouter()


# ---------------------------------------------------------------- models


class CookIn(BaseModel):
    material: str = Field(default="", max_length=100000)
    brief: str = Field(default="", max_length=4000)
    scenario_count: int = Field(default=3, ge=1, le=10)


class GradeStepIn(BaseModel):
    step_key: str = Field(min_length=1, max_length=40)
    choice_index: int = Field(ge=0, le=10)
    steps_correct: int | None = Field(default=None, ge=0, le=10)
    steps_total: int | None = Field(default=None, ge=1, le=10)


class ArtifactIn(BaseModel):
    kind: str = Field(default="code", max_length=20)
    language: str = Field(default="", max_length=40)
    label: str = Field(default="", max_length=100)
    content: str = Field(default="", max_length=20000)


class StepIn(BaseModel):
    key: str = Field(min_length=1, max_length=40)
    question: str = Field(min_length=1, max_length=2000)
    options: list[str] = Field(min_length=2, max_length=6)
    correct_index: int = Field(ge=0, le=10)
    explanation: str = Field(default="", max_length=2000)


class CurateScenarioIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    scenario_type: str = Field(default="code_reading", max_length=40)
    case: dict = Field(default_factory=dict)
    steps: list[StepIn] = Field(min_length=1, max_length=6)
    difficulty: str = Field(default="medium", pattern="^(easy|medium|hard)$")
    tags: list[str] = Field(default_factory=list, max_length=12)
    published: bool = False
    review_status: str = Field(default="draft", max_length=20)


class CuratePatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    scenario_type: str | None = Field(default=None, max_length=40)
    case: dict | None = None
    steps: list[StepIn] | None = Field(default=None, min_length=1, max_length=6)
    difficulty: str | None = Field(default=None, pattern="^(easy|medium|hard)$")
    tags: list[str] | None = Field(default=None, max_length=12)
    published: bool | None = None
    review_status: str | None = Field(default=None, max_length=20)


class ReviewPatchIn(BaseModel):
    review_status: str = Field(pattern="^(draft|pending_review|approved|rejected)$")
    published: bool | None = None
    reason: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------- helpers


def _load_debug_assertion(
    db: Session, assertion_id: uuid.UUID, *, include_retracted: bool = False
) -> dict:
    status_clause = (
        "a.status IN ('active', 'retracted')" if include_retracted else "a.status = 'active'"
    )
    row = db.execute(
        text(
            f"""
            SELECT a.id, a.payload, a.title, a.recorded_at
            FROM intel.assertion a
            WHERE a.id = :id AND {status_clause}
            """
        ),
        {"id": assertion_id},
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    payload = row._mapping["payload"]
    if not isinstance(payload, dict):
        payload = json.loads(payload) if payload else {}
    if payload.get("format") != "qb.debug.v1":
        raise HTTPException(status_code=404, detail="Not a debug scenario")
    return {
        "id": row._mapping["id"],
        "payload": payload,
        "title": row._mapping["title"],
        "recorded_at": row._mapping["recorded_at"],
    }


def _require_playable(
    db: Session,
    assertion_id: uuid.UUID,
    user: Account | None,
    guest_id: str | None,
) -> dict:
    problem = _load_debug_assertion(db, assertion_id)
    facet = db.execute(
        text(
            """
            SELECT published, review_status, owner_user_id
            FROM qb.debug_assertion_facets
            WHERE assertion_id = :aid
            """
        ),
        {"aid": assertion_id},
    ).first()
    if facet is None:
        raise HTTPException(status_code=404, detail="Not found")
    m = facet._mapping
    if bool(m["published"]):
        return problem
    if user and user.is_admin:
        return problem
    if user and m["owner_user_id"] and str(m["owner_user_id"]) == str(user.id):
        return problem
    raise HTTPException(status_code=404, detail="Not found")


def _facet_item(row, *, include_admin: bool = False) -> dict:
    m = row._mapping
    item = {
        "id": str(m["assertion_id"]),
        "title": m.get("title") or "",
        "scenario_type": m.get("scenario_type") or "code_reading",
        "difficulty": m.get("difficulty") or "medium",
        "step_count": int(m.get("step_count") or 0),
        "tags": list(m.get("tags") or []),
        "origin": m.get("origin") or "generated",
    }
    if include_admin:
        item["published"] = bool(m.get("published"))
        item["review_status"] = m.get("review_status") or "draft"
        item["cook_job_id"] = str(m["cook_job_id"]) if m.get("cook_job_id") else None
    return item


# ---------------------------------------------------------------- cook


@router.post("/cook", dependencies=[Depends(rate_limit_dependency)])
def start_cook(
    body: CookIn,
    db: Session = Depends(get_db),
    user: Account = Depends(get_current_user),
) -> dict:
    """Start a private cook job from pasted material or a brief."""
    try:
        job = enqueue_cook_from_material(
            db,
            owner_user_id=user.id,
            material=body.material,
            brief=body.brief,
            scenario_count=body.scenario_count,
            origin="contributed",
        )
        return job
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/cook/{job_id}")
def get_cook_status(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account = Depends(get_current_user),
) -> dict:
    try:
        job = get_cook_job(db, job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc
    if job["owner_user_id"] and job["owner_user_id"] != str(user.id) and not user.is_admin:
        raise HTTPException(status_code=404, detail="Not found")
    return job


@router.post("/cook/{job_id}/submit-to-library", dependencies=[Depends(require_csrf)])
def submit_cook_to_library(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account = Depends(get_current_user),
) -> dict:
    try:
        job = submit_job_to_library(db, job_id, user.id)
        db.commit()
        return job
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------- admin (static paths before /{assertion_id})


@router.get("/admin/list", dependencies=[Depends(require_admin)])
def admin_list(
    db: Session = Depends(get_db),
    review_status: str | None = Query(default=None),
    published: bool | None = Query(default=None),
) -> dict:
    clauses = ["a.status IN ('active', 'retracted')"]
    params: dict = {}
    if review_status:
        clauses.append("f.review_status = :review_status")
        params["review_status"] = review_status
    if published is not None:
        clauses.append("f.published = :published")
        params["published"] = published
    where = " AND ".join(clauses)
    rows = db.execute(
        text(
            f"""
            SELECT f.assertion_id, f.title, f.scenario_type, f.difficulty,
                   f.step_count, f.tags, f.origin, f.published, f.review_status,
                   f.cook_job_id
            FROM qb.debug_assertion_facets f
            JOIN intel.assertion a ON a.id = f.assertion_id
            WHERE {where}
            ORDER BY a.recorded_at DESC
            LIMIT 200
            """
        ),
        params,
    ).fetchall()
    return {"items": [_facet_item(r, include_admin=True) for r in rows]}


@router.post("/admin/seed", dependencies=[Depends(require_admin), Depends(require_csrf)])
def admin_seed(db: Session = Depends(get_db)) -> dict:
    result = seed_starter_bank(db)
    db.commit()
    return result


@router.post("/admin", dependencies=[Depends(require_admin), Depends(require_csrf)])
def admin_create(body: CurateScenarioIn, db: Session = Depends(get_db)) -> dict:
    try:
        pub = upsert_curated_scenario(
            db,
            title=body.title,
            scenario_type=body.scenario_type,
            case=body.case,
            steps=[s.model_dump() for s in body.steps],
            difficulty=body.difficulty,
            tags=body.tags,
            published=body.published,
            review_status=body.review_status,
        )
        db.commit()
        return pub
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/admin/{assertion_id}", dependencies=[Depends(require_admin)])
def admin_get(assertion_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    problem = _load_debug_assertion(db, assertion_id, include_retracted=True)
    out = editorial_payload(problem["payload"])
    out["id"] = str(assertion_id)
    facet = db.execute(
        text(
            "SELECT published, review_status FROM qb.debug_assertion_facets WHERE assertion_id = :aid"
        ),
        {"aid": assertion_id},
    ).first()
    if facet:
        out["published"] = bool(facet._mapping["published"])
        out["review_status"] = facet._mapping["review_status"]
    return out


@router.patch("/admin/{assertion_id}", dependencies=[Depends(require_admin), Depends(require_csrf)])
def admin_patch(
    assertion_id: uuid.UUID,
    body: CuratePatchIn,
    db: Session = Depends(get_db),
) -> dict:
    existing = _load_debug_assertion(db, assertion_id, include_retracted=True)
    payload = existing["payload"]
    title = body.title or payload.get("title") or ""
    try:
        pub = upsert_curated_scenario(
            db,
            title=title,
            scenario_type=body.scenario_type or payload.get("scenario_type") or "code_reading",
            case=body.case if body.case is not None else payload.get("case") or {},
            steps=[s.model_dump() for s in body.steps]
            if body.steps is not None
            else payload.get("steps") or [],
            difficulty=body.difficulty or payload.get("difficulty") or "medium",
            tags=body.tags if body.tags is not None else payload.get("tags"),
            published=body.published if body.published is not None else False,
            review_status=body.review_status or "draft",
            assertion_id=assertion_id,
        )
        if body.published is not None:
            set_published(db, assertion_id, bool(body.published))
        db.commit()
        return pub
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch(
    "/admin/{assertion_id}/review",
    dependencies=[Depends(require_admin), Depends(require_csrf)],
)
def admin_review(
    assertion_id: uuid.UUID,
    body: ReviewPatchIn,
    db: Session = Depends(get_db),
) -> dict:
    try:
        set_review_status(
            db,
            assertion_id,
            body.review_status,
            published=body.published,
        )
        db.commit()
        return {"id": str(assertion_id), "review_status": body.review_status}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc


@router.delete("/admin/{assertion_id}", dependencies=[Depends(require_admin), Depends(require_csrf)])
def admin_delete(assertion_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    try:
        soft_delete_curated(db, assertion_id)
        db.commit()
        return {"deleted": str(assertion_id)}
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc


# ---------------------------------------------------------------- public bank


@router.get("")
def list_scenarios(
    db: Session = Depends(get_db),
    difficulty: str | None = Query(default=None),
    scenario_type: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: Account | None = Depends(get_optional_user),
    guest=Depends(optional_guest_session),
) -> dict:
    """Browse published debug scenarios."""
    del user, guest
    clauses = ["f.published = true", "a.status = 'active'"]
    params: dict = {"limit": limit, "offset": offset}
    if difficulty:
        clauses.append("f.difficulty = :difficulty")
        params["difficulty"] = difficulty
    if scenario_type:
        clauses.append("f.scenario_type = :scenario_type")
        params["scenario_type"] = scenario_type
    if tag:
        clauses.append(":tag = ANY(f.tags)")
        params["tag"] = tag.strip().lower()
    where = " AND ".join(clauses)
    rows = db.execute(
        text(
            f"""
            SELECT f.assertion_id, f.title, f.scenario_type, f.difficulty,
                   f.step_count, f.tags, f.origin, f.published, f.review_status,
                   f.cook_job_id
            FROM qb.debug_assertion_facets f
            JOIN intel.assertion a ON a.id = f.assertion_id
            WHERE {where}
            ORDER BY a.recorded_at DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    ).fetchall()
    return {"items": [_facet_item(r) for r in rows], "limit": limit, "offset": offset}


@router.get("/{assertion_id}")
def get_scenario(
    assertion_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest=Depends(optional_guest_session),
) -> dict:
    del guest
    problem = _require_playable(db, assertion_id, user, None)
    pub = public_payload(problem["payload"])
    pub["id"] = str(assertion_id)
    return pub


@router.post("/{assertion_id}/grade-step", dependencies=[Depends(rate_limit_dependency)])
def grade_scenario_step(
    assertion_id: uuid.UUID,
    body: GradeStepIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest=Depends(optional_guest_session),
) -> dict:
    problem = _require_playable(db, assertion_id, user, guest)
    result = grade_step(problem["payload"], body.step_key, body.choice_index)
    if body.steps_correct is not None and body.steps_total is not None:
        subject = resolve_subject_entity(db, user=user, guest_id=guest)
        record_debug_understood(
            db,
            assertion_id=assertion_id,
            subject_entity_id=subject,
            steps_correct=body.steps_correct,
            steps_total=body.steps_total,
        )
        db.commit()
    return result


@router.post("/{assertion_id}/reflect/stream", dependencies=[Depends(rate_limit_dependency)])
async def reflect_stream(
    assertion_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest=Depends(optional_guest_session),
):
    problem = _require_playable(db, assertion_id, user, guest)
    payload = problem["payload"]
    case_summary = str((payload.get("case") or {}).get("summary") or "")
    steps = payload.get("steps") or []
    steps_text = "\n".join(
        f"- {s.get('key')}: {s.get('explanation', '')}" for s in steps if isinstance(s, dict)
    )
    prompt = (
        "You are a calm debugging mentor. In 3-5 short paragraphs, walk through the "
        "debugging process for this scenario: symptom to hypothesis to root cause to "
        "minimal fix approach to how to verify. Be direct, no fluff.\n\n"
        f"Case: {case_summary}\n\nCorrect reasoning:\n{steps_text}"
    )

    async def gen():
        async for chunk in stream_chat_completion(
            [{"role": "user", "content": prompt}],
            db,
            log_tag="debug_reflect",
        ):
            if chunk:
                yield {"event": "token", "data": chunk}

    return EventSourceResponse(gen())
