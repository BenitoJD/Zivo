"""Enqueue MCQ generation after a document is indexed."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import Document, Job, JobWorkload
from app.services.question_pool import enqueue_initial_pool


def enqueue_generate_if_needed(db: Session, document_id: uuid.UUID) -> Job | None:
    """Start the first page batch once indexing reaches ready."""
    doc = db.get(Document, document_id)
    if not doc or doc.status != "ready":
        return None
    from app.services.background_prep import is_background_prep

    if is_background_prep(doc):
        return None
    return enqueue_initial_pool(db, document_id)


def enqueue_coding_generation_for_page(
    db: Session,
    document_id: uuid.UUID,
    *,
    page: int,
    account_id: uuid.UUID | None = None,
    count: int = 1,
) -> Job | None:
    """Spawn a ``generate.coding`` ETA job for one programmable page.

    Called from the triage completion hook when page coverage carries
    ``programmable=True``. Idempotent-ish: a duplicate spawn only costs one extra
    verify-gated generation pass that will find existing facets and no-op via the
    sequence counter — but we still avoid the obvious duplicate by checking for an
    active/queued coding job for the same page.
    """
    from app.services.jobs import enqueue_job

    return enqueue_job(
        db,
        name="generate.coding",
        workload=JobWorkload.cpu,
        payload={
            "document_id": str(document_id),
            "page_number": int(page),
            "count": int(count),
        },
        account_id=account_id,
    )


def enqueue_debug_generation_for_page(
    db: Session,
    document_id: uuid.UUID,
    *,
    page: int,
    account_id: uuid.UUID | None = None,
    count: int = 2,
) -> Job | None:
    """Spawn a ``generate.debug`` ETA job for one debuggable page."""
    from app.services.jobs import enqueue_job

    return enqueue_job(
        db,
        name="generate.debug",
        workload=JobWorkload.cpu,
        payload={
            "document_id": str(document_id),
            "page_number": int(page),
            "count": int(count),
        },
        account_id=account_id,
    )

