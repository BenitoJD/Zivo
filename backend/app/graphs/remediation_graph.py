"""Remediation variant generation."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.graphs.generation_graph import _generate_one_for_page, _persist_assertion


def generate_remediation(db: Session, source_assertion_id: uuid.UUID, document_id: uuid.UUID) -> dict[str, Any]:
    payload = _generate_one_for_page(
        db,
        page_text="",
        page_number=1,
        sequence=0,
        document_id=document_id,
    )
    if payload is None:
        return {"error": "quality_gate_failed"}
    payload["remediation_of"] = str(source_assertion_id)
    _persist_assertion(db, document_id, payload, page_number=1, sequence=0)
    db.commit()
    return payload
