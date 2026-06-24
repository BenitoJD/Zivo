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


def page_assertion_ids_from_facets(
    db: Session, artifact_id: uuid.UUID, page: int
) -> list[str] | None:
    rows = db.execute(
        text(
            """
            SELECT assertion_id::text
            FROM qb.mcq_assertion_facets
            WHERE artifact_id = :artifact_id AND page_number = :page
            ORDER BY sequence ASC
            """
        ),
        {"artifact_id": artifact_id, "page": page},
    ).scalars().all()
    return list(rows) if rows else None
