"""Shared API access guards.

One place for the "fetch a document and enforce the caller can access it" check
that every artifact-scoped route needs, so the 404-on-missing-or-forbidden rule
lives in exactly one spot instead of being re-implemented per router.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Account, Document
from app.services.guest import can_access_document


def require_document(
    db: Session,
    artifact_id: uuid.UUID,
    user: Account | None,
    guest_id: str | None,
) -> Document:
    """Return the document, or 404 if it's missing or the caller can't access it."""
    doc = db.get(Document, artifact_id)
    if not doc or not can_access_document(doc, user, guest_id):
        raise HTTPException(status_code=404, detail="Not found")
    return doc


def require_ready_document(
    db: Session,
    artifact_id: uuid.UUID,
    user: Account | None,
    guest_id: str | None,
) -> Document | None:
    """Like require_document, but returns None when the doc exists + is accessible
    yet isn't finished indexing (caller renders an 'indexing' state)."""
    doc = require_document(db, artifact_id, user, guest_id)
    return doc if doc.status == "ready" else None


def forbid_newspaper_source(doc: Document, user: Account | None) -> None:
    """Newspaper PDFs/text are practice-only — learners must not fetch source bytes.

    Editions are ``is_public`` so MCQ practice works, but file/pages/segments
    endpoints must stay admin-only (copyright + product intent).
    """
    from app.services.newspaper import is_newspaper_document

    if is_newspaper_document(doc) and (user is None or not getattr(user, "is_admin", False)):
        raise HTTPException(status_code=404, detail="Not found")


def require_document_source(
    db: Session,
    artifact_id: uuid.UUID,
    user: Account | None,
    guest_id: str | None,
) -> Document:
    """require_document + newspaper source gate for PDF/text dump routes."""
    doc = require_document(db, artifact_id, user, guest_id)
    forbid_newspaper_source(doc, user)
    return doc
