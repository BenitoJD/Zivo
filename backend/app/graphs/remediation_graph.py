"""Remediation variant generation."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.graphs.generation_graph import _generate_one_stub, _persist_assertion


def generate_remediation(db: Session, source_assertion_id: uuid.UUID, document_id: uuid.UUID) -> dict[str, Any]:
    payload = _generate_one_stub(db, document_id, 0)
    payload["remediation_of"] = str(source_assertion_id)
    _persist_assertion(db, document_id, payload)
    db.commit()
    return payload
