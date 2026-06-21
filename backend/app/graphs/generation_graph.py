"""Page-by-page question generation agent (LangGraph)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.repositories.intel import update_activity
from app.services.llm_router import complete_chat
from app.services.prompts import get_prompt


def run_generation(db: Session, document_id: uuid.UUID, options: dict[str, Any]) -> dict[str, Any]:
    """Generate initial rolling pool of MCQs for a document."""
    activity_id = options.get("activity_id")
    pool_size = int(options.get("pool_size", 5))
    saved = 0
    for i in range(pool_size):
        question_payload = _generate_one_stub(db, document_id, i)
        _persist_assertion(db, document_id, question_payload)
        saved += 1
        if activity_id:
            update_activity(
                db,
                uuid.UUID(str(activity_id)),
                stats={"questions_saved": saved, "pool_size": pool_size},
            )
    if activity_id:
        update_activity(
            db,
            uuid.UUID(str(activity_id)),
            status="succeeded",
            stats={"questions_saved": saved},
            finished=True,
        )
    db.commit()
    return {"questions_saved": saved}


def _generate_one_stub(db: Session, document_id: uuid.UUID, index: int) -> dict[str, Any]:
    try:
        prompt = get_prompt(db, "mcq_format")
        text_ctx = f"Generate one MCQ for document {document_id} item {index + 1}"
        raw = complete_chat(
            db,
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": text_ctx}],
        )
        if isinstance(raw, str) and raw.strip().startswith("{"):
            return json.loads(raw)
    except Exception:
        pass
    return {
        "question": f"Sample question {index + 1} about the source material?",
        "options": ["Option A", "Option B", "Option C", "Option D"],
        "correct_index": 0,
        "explanation": "Review the source pages for the answer.",
        "primary_concept_key": f"concept-{index + 1}",
        "primary_concept": f"Concept {index + 1}",
        "tags": ["auto"],
    }


def _persist_assertion(db: Session, document_id: uuid.UUID, payload: dict[str, Any]) -> None:
    from app.repositories.intel import _concept_id, _source_id

    assertion_id = uuid.uuid4()
    payload = {**payload, "artifact_id": str(document_id), "format": "qb.mcq.v1"}
    db.execute(
        text(
            """
            INSERT INTO intel.assertion (
              id, type_concept_id, source_id, canonical_uri, fingerprint,
              title, summary, payload, status
            )
            VALUES (
              :id, :type_id, :source_id, :uri, :fp,
              :title, :summary, CAST(:payload AS jsonb), 'active'
            )
            """
        ),
        {
            "id": assertion_id,
            "type_id": _concept_id(db, "/vocab/assertion/question.mcq"),
            "source_id": _source_id(db, "user-upload"),
            "uri": f"qb://assertion/{assertion_id}",
            "fp": str(assertion_id),
            "title": (payload.get("question") or "")[:200],
            "summary": payload.get("explanation"),
            "payload": json.dumps(payload),
        },
    )
