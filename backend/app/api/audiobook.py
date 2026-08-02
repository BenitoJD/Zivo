"""Audiobook — request, poll, and listen to a document's TTS narration.

Thin route layer: validate access, enqueue the ``audiobook.build`` ETA job, and
return the render status + per-chunk playback URLs. The engine policy lives in
``app.services.audiobook`` (pure) and the render I/O in
``app.services.audiobook_worker``; the routes only orchestrate.

Kill switch: ``audiobook_enabled`` (default off) keeps these endpoints inert
(404) until the worker image ships Piper + a voices dir.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.access import require_document
from app.config import get_settings
from app.db import get_db
from app.models import Account
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.audiobook_worker import audiobook_status
from app.services.guest_session import guest_session_for_read
from app.services.jobs import enqueue_job
from app.models import JobWorkload
from app.services.rate_limit import rate_limit_dependency

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_enabled() -> None:
    if not get_settings().audiobook_enabled:
        raise HTTPException(status_code=404, detail="Not found")


@router.post(
    "/{document_id}/build",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
def build_audiobook(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Kick off (or resume) TTS rendering for a document. Idempotent."""
    _require_enabled()
    require_document(db, document_id, user, guest_id)
    enqueue_job(
        db,
        name="audiobook.build",
        workload=JobWorkload.io,
        payload={"document_id": str(document_id)},
    )
    db.commit()
    return {"document_id": str(document_id), "queued": True}


@router.get(
    "/{document_id}/status",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
def status_audiobook(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Poll render progress + playback URLs (empty until ready)."""
    _require_enabled()
    require_document(db, document_id, user, guest_id)
    return audiobook_status(db, document_id)
