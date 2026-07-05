"""LeetCode-style coding practice — problem list, run, and submit endpoints.

Two surfaces, one router:
- Workspace (authed, persistent status): ``GET /api/artifacts/{id}/coding``,
  ``GET /api/coding/{assertion_id}``, run + submit. Per-problem solved/attempted
  is read from ``intel.measurement`` under the coding-passed metric.
- Public sampler (anonymous): ``GET /api/coding`` lists problems across all
  sources; run + submit work the same way with a guest subject entity.

Hidden tests and the editorial reference solution NEVER leave the server. The
single chokepoint is ``coding_generation.public_payload`` — every read path
goes through it.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Account
from app.api.access import require_document
from app.services.answer_signal import resolve_subject_entity
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.code_execution import LANGUAGES, run_code, run_tests
from app.services.coding_generation import public_payload, record_coding_submit
from app.services.guest_session import guest_session_for_read
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


# ---------------------------------------------------------------- helpers


def _public_problem_from_row(row) -> dict:
    """Build the public view of one coding problem from a SQL row.

    Expects columns: id, title, difficulty, language_id, sample_test_count,
    hidden_test_count, plus either ``payload`` (jsonb already cast to a dict) or
    a ``payload_raw`` text column the caller parses. Always goes through
    ``public_payload`` so hidden tests / reference solutions are stripped.
    """
    payload = row._mapping.get("payload")
    if payload is None:
        payload_raw = row._mapping.get("payload_raw")
        import json

        payload = json.loads(payload_raw) if payload_raw else {}
    pub = public_payload(payload if isinstance(payload, dict) else {})
    pub["id"] = str(row._mapping["id"])
    if "title" in row._mapping and row._mapping["title"]:
        pub["title"] = row._mapping["title"]
    return pub


def _load_coding_assertion(db: Session, assertion_id: uuid.UUID) -> dict:
    """Fetch one coding assertion's full payload (incl. hidden tests) for grading."""
    row = db.execute(
        text(
            """
            SELECT a.id, a.payload, a.title,
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
        import json

        payload = json.loads(payload) if payload else {}
    if payload.get("format") != "qb.coding.v1":
        raise HTTPException(status_code=404, detail="Not a coding problem")
    return {
        "id": row._mapping["id"],
        "payload": payload,
        "title": row._mapping["title"],
        "artifact_id": payload.get("artifact_id"),
    }


def _status_for(db: Session, assertion_id: uuid.UUID, subject_entity_id: uuid.UUID | None) -> str:
    """Per-learner status: 'solved' | 'new'.

    Coding measurements only record the *solved* transition (see
    ``record_coding_submit``), so a row means solved and no row means new/not-yet-solved.
    """
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
                   f.sample_test_count, f.hidden_test_count, f.page_number, f.sequence
            FROM qb.coding_assertion_facets f
            WHERE f.artifact_id = :aid
            ORDER BY f.page_number ASC, f.sequence ASC
            """
        ),
        {"aid": artifact_id},
    ).all()
    subject_entity_id = resolve_subject_entity(db, user, guest_id)
    items = []
    for r in rows:
        items.append(
            {
                "id": str(r._mapping["id"]),
                "title": r._mapping["title"] or "Untitled",
                "difficulty": r._mapping["difficulty"] or "medium",
                "language_id": r._mapping["language_id"] or 71,
                "sample_test_count": int(r._mapping["sample_test_count"] or 0),
                "hidden_test_count": int(r._mapping["hidden_test_count"] or 0),
                "page_number": int(r._mapping["page_number"] or 0),
                "status": _status_for(db, uuid.UUID(str(r._mapping["id"])), subject_entity_id),
            }
        )
    return {"artifact_id": str(artifact_id), "items": items}


# ---------------------------------------------------------------- public list


@router.get("")
def list_public_problems(
    db: Session = Depends(get_db),
    difficulty: str | None = Query(default=None, pattern="^(easy|medium|hard)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Browse coding problems across all sources (anonymous sampler).

    No per-learner status here — callers who want it hit the workspace list.
    """
    params: dict = {"limit": limit, "offset": offset}
    where = ""
    if difficulty:
        where = "WHERE f.difficulty = :difficulty"
        params["difficulty"] = difficulty
    rows = db.execute(
        text(
            f"""
            SELECT f.assertion_id AS id, f.title, f.difficulty, f.language_id,
                   f.sample_test_count, f.hidden_test_count
            FROM qb.coding_assertion_facets f
            {where}
            ORDER BY f.assertion_id DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    ).all()
    items = [
        {
            "id": str(r._mapping["id"]),
            "title": r._mapping["title"] or "Untitled",
            "difficulty": r._mapping["difficulty"] or "medium",
            "language_id": r._mapping["language_id"] or 71,
            "sample_test_count": int(r._mapping["sample_test_count"] or 0),
            "hidden_test_count": int(r._mapping["hidden_test_count"] or 0),
        }
        for r in rows
    ]
    return {"items": items, "count": len(items)}
    # NOTE: pagination cursor omitted; v1 sample list is small. Add when the bank grows.


@router.get("/{assertion_id}")
def get_problem(
    assertion_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """One problem's public view: statement + sample tests + starter code only.

    Access check: a workspace user must own the source; the public sampler is
    open. We do the workspace check only when the caller is logged in and the
    problem traces to their doc, otherwise allow (public).
    """
    problem = _load_coding_assertion(db, assertion_id)
    # If the caller is logged in and the artifact is theirs, enforce ownership;
    # anonymous callers and non-owners fall through to the public read.
    artifact_id_str = problem["artifact_id"]
    if user and artifact_id_str:
        try:
            require_document(db, uuid.UUID(str(artifact_id_str)), user, guest_id)
        except HTTPException:
            # Not theirs — still readable as a public sample. The problem bank is
            # public-by-default; ownership only gates the workspace list view.
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
    """Compile + run the user's code against optional stdin (the 'Run' button).

    No grading, no hidden tests. Used for the learner's own debug iteration.
    """
    # Touch the assertion to confirm it exists + is a coding problem (404 otherwise).
    _load_coding_assertion(db, assertion_id)
    try:
        return await run_code(body.source, body.language_id, body.stdin)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


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
    """Grade the submission against hidden tests; record the outcome.

    Returns per-case pass/fail with expected/got only on the FIRST failing case —
    enough signal to debug without leaking the full hidden suite. Pass/fail counts
    are always returned.
    """
    problem = _load_coding_assertion(db, assertion_id)
    payload = problem["payload"]
    hidden_tests = payload.get("hidden_tests") or []
    if not hidden_tests:
        raise HTTPException(status_code=409, detail="Problem has no hidden tests")

    try:
        verdict = await run_tests(body.source, body.language_id, hidden_tests)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    passed = int(verdict.get("passed") or 0)
    total = int(verdict.get("total") or len(hidden_tests))
    all_passed = passed == total and total > 0

    # Record the measurement (idempotent on learner+item). Best-effort: never
    # fail a submit over the moat write.
    subject_entity_id = resolve_subject_entity(db, user, guest_id)
    if subject_entity_id is not None:
        record_coding_submit(
            db,
            assertion_id=assertion_id,
            passed=all_passed,
            passed_count=passed,
            total_count=total,
            language_id=body.language_id,
            subject_entity_id=subject_entity_id,
        )
        db.commit()

    # Reveal only the first failing case (expected vs got); passing cases stay opaque.
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

    return {
        "passed": passed,
        "total": total,
        "all_passed": all_passed,
        "cases": cases_out,
        "error": verdict.get("error"),
        "status": _status_for(db, assertion_id, subject_entity_id) if subject_entity_id else ("solved" if all_passed else "new"),
    }
