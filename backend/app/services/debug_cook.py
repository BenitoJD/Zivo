"""Debug cook job lifecycle — intake, enqueue, status, library submission."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import choose, pick
from app.services.open_response import (
    evaluate_debug_cook_input,
    plan_debug_cook_store_caps,
    plan_debug_cook_yield,
)

logger = logging.getLogger(__name__)

_VALID_SOURCE_TYPES = frozenset({"paste", "upload", "document_page", "admin_author"})
_VALID_ORIGINS = frozenset({"generated", "curated", "contributed"})


def create_cook_job(
    db: Session,
    *,
    owner_user_id: uuid.UUID | None,
    material: str,
    brief: str = "",
    scenario_count: int = 3,
    source_type: str = "paste",
    source_ref: str | None = None,
    origin: str = "contributed",
) -> dict[str, Any]:
    st = choose(source_type in _VALID_SOURCE_TYPES, source_type, "paste")
    og = choose(origin in _VALID_ORIGINS, origin, "contributed")
    count = plan_debug_cook_yield(scenario_count)
    material_clean = (material or "").strip()
    intake = evaluate_debug_cook_input(material=material_clean, brief=brief)

    def _bad() -> None:
        raise ValueError("Provide material or a cook brief")

    pick(not intake.ok, _bad, lambda: None)

    store = plan_debug_cook_store_caps()
    job_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO qb.debug_cook_job (
              id, owner_user_id, source_type, source_ref, material, brief,
              scenario_count, status, origin, review_status, published
            )
            VALUES (
              :id, :owner, :source_type, :source_ref, :material, :brief,
              :count, 'queued', :origin, 'draft', false
            )
            """
        ),
        {
            "id": job_id,
            "owner": owner_user_id,
            "source_type": st,
            "source_ref": source_ref,
            "material": material_clean[: store.material],
            "brief": (brief or "").strip()[: store.brief],
            "count": count,
            "origin": og,
        },
    )
    return get_cook_job(db, job_id)


def get_cook_job(db: Session, job_id: uuid.UUID) -> dict[str, Any]:
    row = db.execute(
        text(
            """
            SELECT id, owner_user_id, source_type, source_ref, material, brief,
                   scenario_count, status, origin, review_status, published,
                   error, created_at, completed_at
            FROM qb.debug_cook_job
            WHERE id = :id
            """
        ),
        {"id": job_id},
    ).first()

    def _missing() -> None:
        raise LookupError("Cook job not found")

    pick(not row, _missing, lambda: None)
    m = row._mapping
    scenario_ids = db.execute(
        text(
            """
            SELECT assertion_id::text
            FROM qb.debug_assertion_facets
            WHERE cook_job_id = :jid
            ORDER BY sequence, title
            """
        ),
        {"jid": job_id},
    ).scalars().all()
    return {
        "id": str(m["id"]),
        "owner_user_id": pick(bool(m["owner_user_id"]), lambda: str(m["owner_user_id"]), lambda: None),
        "source_type": m["source_type"],
        "source_ref": m["source_ref"],
        "brief": m["brief"],
        "scenario_count": m["scenario_count"],
        "status": m["status"],
        "origin": m["origin"],
        "review_status": m["review_status"],
        "published": bool(m["published"]),
        "error": m["error"],
        "created_at": pick(bool(m["created_at"]), lambda: m["created_at"].isoformat(), lambda: None),
        "completed_at": pick(bool(m["completed_at"]), lambda: m["completed_at"].isoformat(), lambda: None),
        "scenario_ids": list(scenario_ids),
        "material_preview": (m["material"] or "")[:500],
    }


def set_cook_job_status(
    db: Session,
    job_id: uuid.UUID,
    status: str,
    *,
    error: str | None = None,
) -> None:
    completed = choose(status in ("done", "failed"), ", completed_at = now()", "")
    db.execute(
        text(
            f"""
            UPDATE qb.debug_cook_job
            SET status = :status, error = :error{completed}
            WHERE id = :id
            """
        ),
        {"id": job_id, "status": status, "error": error},
    )


def submit_job_to_library(db: Session, job_id: uuid.UUID, owner_user_id: uuid.UUID) -> dict[str, Any]:
    job = get_cook_job(db, job_id)

    def _perm() -> None:
        raise PermissionError("Not your cook job")

    def _incomplete() -> None:
        raise ValueError("Cook job is not complete")

    pick(bool(job["owner_user_id"] and job["owner_user_id"] != str(owner_user_id)), _perm, lambda: None)
    pick(job["status"] != "done", _incomplete, lambda: None)
    db.execute(
        text(
            """
            UPDATE qb.debug_cook_job
            SET review_status = 'pending_review'
            WHERE id = :id
            """
        ),
        {"id": job_id},
    )
    db.execute(
        text(
            """
            UPDATE qb.debug_assertion_facets
            SET review_status = 'pending_review'
            WHERE cook_job_id = :jid AND review_status = 'draft'
            """
        ),
        {"jid": job_id},
    )
    return get_cook_job(db, job_id)


def enqueue_cook_job(db: Session, job_id: uuid.UUID, account_id: uuid.UUID | None = None) -> None:
    from app.services.jobs import enqueue_job
    from app.models import JobWorkload

    enqueue_job(
        db,
        name="generate.debug",
        workload=JobWorkload.cpu,
        payload={"cook_job_id": str(job_id)},
        account_id=account_id,
    )


def enqueue_cook_from_material(
    db: Session,
    *,
    owner_user_id: uuid.UUID | None,
    material: str,
    brief: str = "",
    scenario_count: int = 3,
    origin: str = "contributed",
    source_type: str = "paste",
    source_ref: str | None = None,
) -> dict[str, Any]:
    job = create_cook_job(
        db,
        owner_user_id=owner_user_id,
        material=material,
        brief=brief,
        scenario_count=scenario_count,
        source_type=source_type,
        source_ref=source_ref,
        origin=origin,
    )
    db.commit()
    enqueue_cook_job(db, uuid.UUID(job["id"]), owner_user_id)
    return job
