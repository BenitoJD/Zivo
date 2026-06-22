"""Enqueue MCQ generation after a document is indexed."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import Document, Job
from app.services.question_pool import enqueue_initial_pool


def enqueue_generate_if_needed(db: Session, document_id: uuid.UUID) -> Job | None:
    """Start the first page batch once indexing reaches ready."""
    doc = db.get(Document, document_id)
    if not doc or doc.status != "ready":
        return None
    return enqueue_initial_pool(db, document_id)
