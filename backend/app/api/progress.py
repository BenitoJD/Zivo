"""Learner progress journal — lifetime + per-source rollups."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.access import require_document
from app.db import get_db
from app.models import Account
from app.services.answer_signal import resolve_subject_entity
from app.services.auth import get_optional_user
from app.services.guest_session import guest_session_for_read
from app.services.progress import build_learner_progress

router = APIRouter()


@router.get("")
def learner_progress(
    artifact_id: uuid.UUID | None = Query(
        default=None,
        description="Optional source scope — omit for lifetime across sources.",
    ),
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """First-attempt answer accuracy + tutor questions asked.

    Sourced from immutable ``intel.measurement`` rows (same signal as the end-of-
    study report) and ``qb.chat_message`` user turns. Retries are not recounted.
    """
    if artifact_id is not None:
        require_document(db, artifact_id, user, guest_id)
    subject_id = resolve_subject_entity(db, user, guest_id)
    return build_learner_progress(
        db,
        subject_entity_id=subject_id,
        account_id=user.id if user else None,
        guest_id=guest_id,
        artifact_id=artifact_id,
    )
