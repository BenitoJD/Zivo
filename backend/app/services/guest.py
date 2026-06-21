"""Anonymous guest sessions — documents and chat without sign-in."""

from __future__ import annotations

import uuid

from fastapi import Response
from sqlalchemy.orm import Session

from app.models import Account, ChatThread, Document, User
from app.services.usage import DEMO_COOKIE, ensure_demo_cookie

__all__ = [
    "DEMO_COOKIE",
    "can_access_document",
    "claim_guest_documents",
    "document_owned_by_guest",
    "ensure_guest_id",
    "guest_id_from_cookie",
]


def ensure_guest_id(response: Response | None, cookie_id: str | None) -> str:
    return ensure_demo_cookie(response, cookie_id)


def guest_id_from_cookie(cookie_id: str | None) -> str | None:
    return cookie_id or None


def claim_guest_documents(db: Session, account_id: uuid.UUID, guest_id: str | None) -> list[uuid.UUID]:
    """Attach anonymous uploads to the account that just signed in."""
    if not guest_id:
        return []

    docs = (
        db.query(Document)
        .filter(Document.account_id.is_(None))
        .filter(Document.meta["guest_id"].astext == guest_id)
        .all()
    )
    if not docs:
        return []

    claimed: list[uuid.UUID] = []
    for doc in docs:
        doc.account_id = account_id
        meta = dict(doc.meta or {})
        meta.pop("guest_id", None)
        doc.meta = meta
        claimed.append(doc.id)

        db.query(ChatThread).filter(
            ChatThread.artifact_id == (doc.artifact_id or doc.id),
            ChatThread.account_id.is_(None),
        ).update({ChatThread.account_id: account_id}, synchronize_session=False)

    db.commit()
    return claimed


def document_owned_by_guest(doc: Document, guest_id: str | None) -> bool:
    if doc.account_id is not None:
        return False
    if doc.meta and doc.meta.get("is_demo"):
        return False
    return bool(guest_id) and doc.meta.get("guest_id") == guest_id


def can_access_document(doc: Document, user: Account | None, guest_id: str | None) -> bool:
    if doc.account_id is not None:
        return user is not None and doc.account_id == user.id
    if doc.meta and doc.meta.get("is_demo"):
        return True
    return document_owned_by_guest(doc, guest_id)
