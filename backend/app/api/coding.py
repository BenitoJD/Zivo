"""LeetCode-style coding practice — browse, solve, and admin curate.

Surfaces:
- Workspace (authed): ``GET /api/coding/workspace/{artifact_id}`` — problems for one source
- Public bank: ``GET /api/coding`` — published problems (generated + curated)
- Admin curate: ``/api/coding/admin*`` — create/edit/publish/seed
- Run + submit: Judge0 via ``code_execution``

Hidden tests and the editorial reference solution NEVER leave the server on
public reads. The chokepoint is ``coding_generation.public_payload``. Admin
editorial reads use ``coding_curation.editorial_payload``.
"""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.api.access import require_document
from app.db import get_db
from app.models import Account
from app.services.answer_signal import resolve_subject_entity
from app.services.auth import get_optional_user, require_admin, require_csrf, require_csrf_or_guest
from app.services.code_execution import LANGUAGES, ensure_language, run_code, run_tests
from app.services.coding_assist import (
    assist_event_stream,
    clear_assist_thread,
    list_assist_messages,
    prepare_assist_stream,
)
from app.services.coding_curation import (
    editorial_payload,
    seed_starter_bank,
    set_published,
    soft_delete_curated,
    upsert_curated_problem,
)
from app.services.coding_generation import public_payload, record_coding_submit
from app.services.coding_teach_gap import teach_after_submit
from app.services.guest_session import guest_session_for_read, optional_guest_session
from app.services.rate_limit import rate_limit_dependency

router = APIRouter()


# ---------------------------------------------------------------- languages (declared before /{assertion_id} so the static path wins)


@router.get("/meta/languages")
def languages() -> dict:
    """Languages the coding editor can offer (Judge0 id → label)."""
    return {"languages": [{"id": k, "label": v} for k, v in LANGUAGES.items()]}


# ---------------------------------------------------------------- models


class RunCodeIn(BaseModel):
    source: str = Field(default="", max_length=50000)
    language_id: int = 71
    stdin: str = Field(default="", max_length=20000)


class SubmitIn(BaseModel):
    source: str = Field(default="", max_length=50000)
    language_id: int = 71


class TestCaseIn(BaseModel):
    stdin: str = Field(default="", max_length=20000)
    expected_output: str = Field(default="", max_length=20000)


class CurateProblemIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1, max_length=50000)
    starter_code: str = Field(default="", max_length=50000)
    tests: list[TestCaseIn] = Field(min_length=3, max_length=40)
    difficulty: str = Field(default="medium", pattern="^(easy|medium|hard)$")
    language_id: int = 71
    concept: str = Field(default="", max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=12)
    editor_solution: str = Field(default="", max_length=50000)
    published: bool = True
    slug: str | None = Field(default=None, max_length=80)


class CuratePatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    statement: str | None = Field(default=None, min_length=1, max_length=50000)
    starter_code: str | None = Field(default=None, max_length=50000)
    tests: list[TestCaseIn] | None = Field(default=None, min_length=3, max_length=40)
    difficulty: str | None = Field(default=None, pattern="^(easy|medium|hard)$")
    language_id: int | None = None
    concept: str | None = Field(default=None, max_length=200)
    tags: list[str] | None = Field(default=None, max_length=12)
    editor_solution: str | None = Field(default=None, max_length=50000)
    published: bool | None = None


class AssistIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    code: str | None = Field(default=None, max_length=50000)
    language_id: int | None = None
    stdin: str | None = Field(default=None, max_length=20000)
    last_status: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------- helpers


def _load_coding_assertion(db: Session, assertion_id: uuid.UUID) -> dict:
    """Fetch one coding assertion's full payload (incl. hidden tests) for grading."""
    row = db.execute(
        text(
            """
            SELECT a.id, a.payload, a.title, a.recorded_at,
                   (a.payload->>'artifact_id') AS artifact_id
            FROM intel.assertion a
            WHERE a.id = :id AND a.status = 'active'
            """
        ),
        {"id": assertion_id},
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    payload = row._mapping["payload"]
    if not isinstance(payload, dict):
        payload = json.loads(payload) if payload else {}
    if payload.get("format") != "qb.coding.v1":
        raise HTTPException(status_code=404, detail="Not a coding problem")
    return {
        "id": row._mapping["id"],
        "payload": payload,
        "title": row._mapping["title"],
        "recorded_at": row._mapping["recorded_at"],
        "artifact_id": payload.get("artifact_id"),
    }


def _require_solvable_problem(
    db: Session,
    assertion_id: uuid.UUID,
    user: Account | None,
) -> dict:
    """Load a coding problem the caller may practice (published, or admin draft)."""
    problem = _load_coding_assertion(db, assertion_id)
    facet = db.execute(
        text(
            """
            SELECT published FROM qb.coding_assertion_facets WHERE assertion_id = :aid
            """
        ),
        {"aid": assertion_id},
    ).first()
    if facet is not None and not bool(facet._mapping["published"]):
        if not user or not user.is_admin:
            raise HTTPException(status_code=404, detail="Not found")
    return problem


def _validate_language_id(language_id: int) -> int:
    try:
        return ensure_language(language_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _status_for(db: Session, assertion_id: uuid.UUID, subject_entity_id: uuid.UUID | None) -> str:
    """Per-learner status: 'solved' | 'new'."""
    if subject_entity_id is None:
        return "new"
    row = db.execute(
        text(
            """
            SELECT 1
            FROM intel.measurement
            WHERE subject_entity_id = :entity
              AND source_assertion_id = :aid
              AND metric_concept_id = (
                SELECT id FROM intel.concept WHERE uri = '/vocab/metric/coding.passed'
              )
            LIMIT 1
            """
        ),
        {"entity": subject_entity_id, "aid": assertion_id},
    ).scalar()
    return "solved" if row else "new"


def _facet_item(row, *, status: str | None = None, include_unpublished: bool = False) -> dict:
    m = row._mapping
    tags = m.get("tags") or []
    if not isinstance(tags, list):
        tags = list(tags) if tags else []
    item = {
        "id": str(m["id"]),
        "title": m["title"] or "Untitled",
        "difficulty": m["difficulty"] or "medium",
        "language_id": m["language_id"] or 71,
        "sample_test_count": int(m["sample_test_count"] or 0),
        "hidden_test_count": int(m["hidden_test_count"] or 0),
        "tags": tags,
        "origin": m.get("origin") or "generated",
        "concept": m.get("concept") or "",
    }
    if "page_number" in m and m["page_number"] is not None:
        item["page_number"] = int(m["page_number"] or 0)
    if status is not None:
        item["status"] = status
    if include_unpublished and "published" in m:
        item["published"] = bool(m["published"])
    return item


# ---------------------------------------------------------------- admin curate (before /{id})


@router.get("/admin", dependencies=[Depends(require_admin)])
def admin_list_problems(
    db: Session = Depends(get_db),
    published: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """All coding problems including drafts (admin)."""
    clauses: list[str] = []
    params: dict = {"limit": limit, "offset": offset}
    if published is not None:
        clauses.append("f.published = :published")
        params["published"] = published
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = db.execute(
        text(
            f"""
            SELECT f.assertion_id AS id, f.title, f.difficulty, f.language_id,
                   f.sample_test_count, f.hidden_test_count, f.published, f.origin,
                   f.tags, COALESCE(a.payload->>'concept', '') AS concept
            FROM qb.coding_assertion_facets f
            JOIN intel.assertion a ON a.id = f.assertion_id
            {where}
            ORDER BY f.origin DESC, f.title ASC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    ).all()
    return {
        "items": [_facet_item(r, include_unpublished=True) for r in rows],
        "count": len(rows),
    }


@router.post("/admin/seed", dependencies=[Depends(require_admin), Depends(require_csrf)])
def admin_seed_bank(db: Session = Depends(get_db)) -> dict:
    """Idempotently seed the classic curated starter problems."""
    try:
        result = seed_starter_bank(db)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Seed failed: {exc}") from exc
    return result


@router.post("/admin", dependencies=[Depends(require_admin), Depends(require_csrf)])
def admin_create_problem(body: CurateProblemIn, db: Session = Depends(get_db)) -> dict:
    """Create a curated coding problem (human-authored)."""
    try:
        pub = upsert_curated_problem(
            db,
            title=body.title,
            statement=body.statement,
            starter_code=body.starter_code,
            tests=[t.model_dump() for t in body.tests],
            difficulty=body.difficulty,
            language_id=body.language_id,
            concept=body.concept,
            tags=body.tags,
            editor_solution=body.editor_solution,
            published=body.published,
            slug=body.slug,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return pub


@router.get("/admin/{assertion_id}", dependencies=[Depends(require_admin)])
def admin_get_problem(assertion_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """Editorial view — hidden tests + reference solution included."""
    problem = _load_coding_assertion(db, assertion_id)
    pub = editorial_payload(problem["payload"])
    pub["id"] = str(assertion_id)
    facet = db.execute(
        text(
            """
            SELECT published, origin, tags
            FROM qb.coding_assertion_facets
            WHERE assertion_id = :aid
            """
        ),
        {"aid": assertion_id},
    ).first()
    if facet:
        pub["published"] = bool(facet._mapping["published"])
        pub["origin"] = facet._mapping["origin"] or pub.get("origin")
        tags = facet._mapping["tags"] or []
        pub["tags"] = list(tags) if not isinstance(tags, list) else tags
    return pub


@router.patch("/admin/{assertion_id}", dependencies=[Depends(require_admin), Depends(require_csrf)])
def admin_patch_problem(
    assertion_id: uuid.UUID,
    body: CuratePatchIn,
    db: Session = Depends(get_db),
) -> dict:
    """Update a curated (or imported) problem. Publish-only patches allowed on any."""
    problem = _load_coding_assertion(db, assertion_id)
    payload = problem["payload"]

    # Publish toggle alone — works for generated + curated.
    only_publish = (
        body.published is not None
        and body.title is None
        and body.statement is None
        and body.starter_code is None
        and body.tests is None
        and body.difficulty is None
        and body.language_id is None
        and body.concept is None
        and body.tags is None
        and body.editor_solution is None
    )
    if only_publish:
        try:
            set_published(db, assertion_id, bool(body.published))
            db.commit()
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Not found") from exc
        out = public_payload(payload)
        out["id"] = str(assertion_id)
        out["published"] = bool(body.published)
        return out

    # Full rewrite path — merge with existing payload.
    existing_tests = list(payload.get("sample_tests") or []) + list(payload.get("hidden_tests") or [])
    tests = [t.model_dump() for t in body.tests] if body.tests is not None else existing_tests
    try:
        pub = upsert_curated_problem(
            db,
            assertion_id=assertion_id,
            title=body.title if body.title is not None else str(payload.get("title") or problem["title"] or ""),
            statement=body.statement if body.statement is not None else str(payload.get("statement") or ""),
            starter_code=body.starter_code if body.starter_code is not None else str(payload.get("starter_code") or ""),
            tests=tests,
            difficulty=body.difficulty if body.difficulty is not None else str(payload.get("difficulty") or "medium"),
            language_id=body.language_id if body.language_id is not None else int(payload.get("language_id") or 71),
            concept=body.concept if body.concept is not None else str(payload.get("concept") or ""),
            tags=body.tags if body.tags is not None else list(payload.get("tags") or []),
            editor_solution=(
                body.editor_solution
                if body.editor_solution is not None
                else str(payload.get("editor_solution") or "")
            ),
            published=bool(body.published) if body.published is not None else True,
            slug=None,
        )
        # Preserve published flag from facet if not in body.
        if body.published is None:
            facet_pub = db.execute(
                text("SELECT published FROM qb.coding_assertion_facets WHERE assertion_id = :aid"),
                {"aid": assertion_id},
            ).scalar()
            if facet_pub is not None:
                set_published(db, assertion_id, bool(facet_pub))
                pub["published"] = bool(facet_pub)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return pub


@router.delete("/admin/{assertion_id}", dependencies=[Depends(require_admin), Depends(require_csrf)])
def admin_delete_problem(assertion_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """Soft-delete: unpublish + inactive assertion."""
    _load_coding_assertion(db, assertion_id)
    soft_delete_curated(db, assertion_id)
    db.commit()
    return {"ok": True, "id": str(assertion_id)}


# ---------------------------------------------------------------- workspace list


@router.get("/workspace/{artifact_id}")
def list_workspace_problems(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """All coding problems generated for one uploaded source, with per-learner status."""
    require_document(db, artifact_id, user, guest_id)
    rows = db.execute(
        text(
            """
            SELECT f.assertion_id AS id, f.title, f.difficulty, f.language_id,
                   f.sample_test_count, f.hidden_test_count, f.page_number, f.sequence,
                   f.tags, f.origin, COALESCE(a.payload->>'concept', '') AS concept
            FROM qb.coding_assertion_facets f
            JOIN intel.assertion a ON a.id = f.assertion_id
            WHERE f.artifact_id = :aid
              AND COALESCE(f.published, true) = true
              AND a.status = 'active'
            ORDER BY f.page_number ASC, f.sequence ASC
            """
        ),
        {"aid": artifact_id},
    ).all()
    subject_entity_id = resolve_subject_entity(db, user, guest_id)
    items = [
        _facet_item(r, status=_status_for(db, uuid.UUID(str(r._mapping["id"])), subject_entity_id))
        for r in rows
    ]
    return {"artifact_id": str(artifact_id), "items": items}


# ---------------------------------------------------------------- public list


@router.get("")
def list_public_problems(
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
    difficulty: str | None = Query(default=None, pattern="^(easy|medium|hard)$"),
    tag: str | None = Query(default=None, max_length=40),
    status: str | None = Query(default=None, pattern="^(new|solved)$"),
    origin: str | None = Query(default=None, pattern="^(generated|curated)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Browse published coding problems (curated bank + generated from sources)."""
    params: dict = {"limit": limit, "offset": offset}
    clauses = [
        "COALESCE(f.published, true) = true",
        "a.status = 'active'",
    ]
    if difficulty:
        clauses.append("f.difficulty = :difficulty")
        params["difficulty"] = difficulty
    if tag:
        clauses.append(":tag = ANY(f.tags)")
        params["tag"] = tag.strip().lower()
    if origin:
        clauses.append("f.origin = :origin")
        params["origin"] = origin

    where = " AND ".join(clauses)
    rows = db.execute(
        text(
            f"""
            SELECT f.assertion_id AS id, f.title, f.difficulty, f.language_id,
                   f.sample_test_count, f.hidden_test_count, f.tags, f.origin,
                   COALESCE(a.payload->>'concept', '') AS concept
            FROM qb.coding_assertion_facets f
            JOIN intel.assertion a ON a.id = f.assertion_id
            WHERE {where}
            ORDER BY
              CASE f.difficulty WHEN 'easy' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
              f.title ASC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    ).all()

    subject_entity_id = resolve_subject_entity(db, user, guest_id)
    items = []
    for r in rows:
        st = _status_for(db, uuid.UUID(str(r._mapping["id"])), subject_entity_id)
        if status and st != status:
            continue
        items.append(_facet_item(r, status=st))

    # Distinct tags for filter chips (published only).
    tag_rows = db.execute(
        text(
            """
            SELECT DISTINCT UNNEST(f.tags) AS tag
            FROM qb.coding_assertion_facets f
            JOIN intel.assertion a ON a.id = f.assertion_id
            WHERE COALESCE(f.published, true) = true AND a.status = 'active'
            ORDER BY 1
            LIMIT 100
            """
        )
    ).all()
    all_tags = [str(r[0]) for r in tag_rows if r[0]]

    return {"items": items, "count": len(items), "tags": all_tags}


@router.get("/{assertion_id}")
def get_problem(
    assertion_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """One problem's public view: statement + sample tests + starter code only."""
    problem = _load_coding_assertion(db, assertion_id)
    # Unpublished problems are admin-only on the public get.
    facet = db.execute(
        text(
            """
            SELECT published FROM qb.coding_assertion_facets WHERE assertion_id = :aid
            """
        ),
        {"aid": assertion_id},
    ).first()
    if facet is not None and not bool(facet._mapping["published"]):
        if not user or not user.is_admin:
            raise HTTPException(status_code=404, detail="Not found")

    artifact_id_str = problem["artifact_id"]
    if user and artifact_id_str:
        try:
            require_document(db, uuid.UUID(str(artifact_id_str)), user, guest_id)
        except HTTPException:
            pass
        except (ValueError, TypeError):
            pass

    pub = public_payload(problem["payload"])
    pub["id"] = str(assertion_id)
    subject_entity_id = resolve_subject_entity(db, user, guest_id)
    pub["status"] = _status_for(db, assertion_id, subject_entity_id)
    return pub


# ---------------------------------------------------------------- run (sample / custom)


@router.post(
    "/{assertion_id}/run",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
async def run_problem_code(
    assertion_id: uuid.UUID,
    body: RunCodeIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Compile + run the user's code against optional stdin (the 'Run' button)."""
    _ = user, guest_id
    _load_coding_assertion(db, assertion_id)
    language_id = _validate_language_id(body.language_id)
    try:
        return await run_code(body.source, language_id, body.stdin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ---------------------------------------------------------------- assist (assertion-scoped tutor)


@router.get("/{assertion_id}/assist/messages")
def assist_messages(
    assertion_id: uuid.UUID,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> list[dict]:
    problem = _require_solvable_problem(db, assertion_id, user)
    msgs = list_assist_messages(
        db,
        account_id=user.id if user else None,
        assertion_id=assertion_id,
        recorded_at=problem["recorded_at"],
        guest_id=guest_id,
        offset=offset,
    )
    return [
        {
            "id": str(m.id),
            "role": m.role,
            "content": m.content,
            "citations": m.citations,
        }
        for m in msgs
    ]


@router.post(
    "/{assertion_id}/assist/clear",
    dependencies=[Depends(require_csrf_or_guest)],
)
def assist_clear(
    assertion_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict[str, int]:
    problem = _require_solvable_problem(db, assertion_id, user)
    version = clear_assist_thread(
        db,
        account_id=user.id if user else None,
        assertion_id=assertion_id,
        recorded_at=problem["recorded_at"],
        guest_id=guest_id,
    )
    return {"version": version}


@router.post(
    "/{assertion_id}/assist",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
async def assist_stream(
    assertion_id: uuid.UUID,
    body: AssistIn,
    request: Request,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(optional_guest_session),
) -> EventSourceResponse:
    problem = _require_solvable_problem(db, assertion_id, user)
    if body.language_id is not None:
        _validate_language_id(body.language_id)
    pub = public_payload(problem["payload"])
    recorded_at = problem["recorded_at"]
    # Close request-scoped session before the long-lived SSE generator runs.
    db.close()
    setup = await asyncio.to_thread(
        prepare_assist_stream,
        request=request,
        user=user,
        guest_id=guest_id,
        assertion_id=assertion_id,
        recorded_at=recorded_at,
        public_problem=pub,
        message=body.message,
        code=body.code,
        language_id=body.language_id,
        stdin=body.stdin,
        last_status=body.last_status,
    )
    return await assist_event_stream(setup)


# ---------------------------------------------------------------- submit (hidden tests)


@router.post(
    "/{assertion_id}/submit",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
async def submit_problem(
    assertion_id: uuid.UUID,
    body: SubmitIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Grade the submission against hidden tests; record the outcome."""
    problem = _load_coding_assertion(db, assertion_id)
    payload = problem["payload"]
    hidden_tests = payload.get("hidden_tests") or []
    if not hidden_tests:
        raise HTTPException(status_code=409, detail="Problem has no hidden tests")

    language_id = _validate_language_id(body.language_id)
    try:
        verdict = await run_tests(body.source, language_id, hidden_tests)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    subject_entity_id = resolve_subject_entity(db, user, guest_id)
    status = (
        _status_for(db, assertion_id, subject_entity_id)
        if subject_entity_id
        else "new"
    )

    # Sandbox unreachable / timed out — do not record a fail or invent a teach-gap
    # lesson that blames the learner's code.
    if verdict.get("error"):
        return {
            "passed": 0,
            "total": int(verdict.get("total") or len(hidden_tests)),
            "all_passed": False,
            "cases": [],
            "error": verdict["error"],
            "status": status,
            "mentor_summary": None,
            "weak_concepts": [],
            "lesson": None,
            "recommended_next_id": None,
            "reference_solution": None,
        }

    passed = int(verdict.get("passed") or 0)
    total = int(verdict.get("total") or len(hidden_tests))
    all_passed = passed == total and total > 0

    if subject_entity_id is not None:
        record_coding_submit(
            db,
            assertion_id=assertion_id,
            passed=all_passed,
            passed_count=passed,
            total_count=total,
            language_id=language_id,
            subject_entity_id=subject_entity_id,
        )
        db.commit()
        status = _status_for(db, assertion_id, subject_entity_id)
    elif all_passed:
        status = "solved"

    cases_out: list[dict] = []
    first_fail_shown = False
    for c in verdict.get("cases") or []:
        if c.get("ok"):
            cases_out.append({"ok": True})
        elif not first_fail_shown:
            cases_out.append(
                {
                    "ok": False,
                    "stdin": c.get("stdin", ""),
                    "expected": c.get("expected", ""),
                    "stdout": c.get("stdout", ""),
                    "stderr": c.get("stderr", ""),
                }
            )
            first_fail_shown = True
        else:
            cases_out.append({"ok": False})

    teach = await teach_after_submit(
        db,
        assertion_id=assertion_id,
        payload=payload,
        source=body.source,
        all_passed=all_passed,
        passed=passed,
        total=total,
        cases=cases_out,
        subject_entity_id=subject_entity_id,
    )

    return {
        "passed": passed,
        "total": total,
        "all_passed": all_passed,
        "cases": cases_out,
        "error": None,
        "status": status,
        "mentor_summary": teach["mentor_summary"],
        "weak_concepts": teach["weak_concepts"],
        "lesson": teach["lesson"],
        "recommended_next_id": teach["recommended_next_id"],
        "reference_solution": teach.get("reference_solution"),
    }
