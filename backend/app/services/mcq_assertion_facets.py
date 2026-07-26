"""Indexed MCQ facet rows — hot fields promoted out of intel.assertion.payload."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def upsert_facet(
    db: Session,
    *,
    assertion_id: uuid.UUID,
    artifact_id: uuid.UUID,
    page_number: int,
    sequence: int,
    payload: dict[str, Any],
) -> None:
    question = str(payload.get("question") or payload.get("stem") or "")[:2000]
    try:
        correct_index = int(payload.get("correct_index", 0))
    except (TypeError, ValueError):
        correct_index = 0
    db.execute(
        text(
            """
            INSERT INTO qb.mcq_assertion_facets (
              assertion_id, artifact_id, page_number, sequence, question, correct_index
            )
            VALUES (:aid, :artifact_id, :page, :seq, :question, :ci)
            ON CONFLICT (assertion_id) DO UPDATE SET
              artifact_id = EXCLUDED.artifact_id,
              page_number = EXCLUDED.page_number,
              sequence = EXCLUDED.sequence,
              question = EXCLUDED.question,
              correct_index = EXCLUDED.correct_index
            """
        ),
        {
            "aid": assertion_id,
            "artifact_id": artifact_id,
            "page": page_number,
            "seq": sequence,
            "question": question,
            "ci": correct_index,
        },
    )


def upsert_facets(
    db: Session,
    rows: list[dict[str, Any]],
) -> None:
    """Batched upsert — one executemany for many assertions instead of N round trips.

    Each row: {assertion_id, artifact_id, page_number, sequence, payload}.
    Empty list is a no-op so callers can pass through unconditionally.
    """
    if not rows:
        return
    bound: list[dict[str, Any]] = []
    for r in rows:
        payload = r.get("payload") or {}
        question = str(payload.get("question") or payload.get("stem") or "")[:2000]
        try:
            ci = int(payload.get("correct_index", 0))
        except (TypeError, ValueError):
            ci = 0
        bound.append(
            {
                "aid": r["assertion_id"],
                "artifact_id": r["artifact_id"],
                "page": r["page_number"],
                "seq": r["sequence"],
                "question": question,
                "ci": ci,
            }
        )
    db.execute(
        text(
            """
            INSERT INTO qb.mcq_assertion_facets (
              assertion_id, artifact_id, page_number, sequence, question, correct_index
            )
            VALUES (:aid, :artifact_id, :page, :seq, :question, :ci)
            ON CONFLICT (assertion_id) DO UPDATE SET
              artifact_id = EXCLUDED.artifact_id,
              page_number = EXCLUDED.page_number,
              sequence = EXCLUDED.sequence,
              question = EXCLUDED.question,
              correct_index = EXCLUDED.correct_index
            """
        ),
        bound,
    )


def page_assertion_ids_from_facets(
    db: Session,
    artifact_id: uuid.UUID,
    page: int,
    *,
    serve_mode: str | None = None,
) -> list[str] | None:
    mode_filter = ""
    params: dict[str, Any] = {"artifact_id": artifact_id, "page": page}
    if serve_mode is not None:
        mode_filter = (
            " AND EXISTS (SELECT 1 FROM intel.assertion a "
            "WHERE a.id = f.assertion_id "
            "AND COALESCE(a.payload->>'serve_mode', 'learn') = :serve_mode)"
        )
        params["serve_mode"] = serve_mode
    rows = db.execute(
        text(
            f"""
            SELECT f.assertion_id::text
            FROM qb.mcq_assertion_facets f
            WHERE f.artifact_id = :artifact_id AND f.page_number = :page
            {mode_filter}
            ORDER BY f.sequence ASC
            """
        ),
        params,
    ).scalars().all()
    if not rows:
        return None if serve_mode is None else []
    return list(rows)
