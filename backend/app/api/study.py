"""Study artifacts — notes, cheat sheets, flashcards (Scribely-style, additive to MCQ)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Account, Document
from app.services import saved_notes as saved_notes_service
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.guest import can_access_document
from app.services.guest_session import guest_session_for_read
from app.services.memory_palace import ensure_palace
from app.services.study_artifacts import NOTE_KINDS, ensure_flashcards, ensure_notes

router = APIRouter()


def _require_ready_doc(
    db: Session, artifact_id: uuid.UUID, user: Account | None, guest_id: str | None
) -> Document | None:
    """404 if missing/forbidden; None (caller returns 'indexing') if not yet ready."""
    doc = db.get(Document, artifact_id)
    if not doc or not can_access_document(doc, user, guest_id):
        raise HTTPException(status_code=404, detail="Not found")
    return doc if doc.status == "ready" else None


def _require_doc(
    db: Session, artifact_id: uuid.UUID, user: Account | None, guest_id: str | None
) -> Document:
    """Access check only (no readiness gate) — for saved notes."""
    doc = db.get(Document, artifact_id)
    if not doc or not can_access_document(doc, user, guest_id):
        raise HTTPException(status_code=404, detail="Not found")
    return doc


@router.get("/{artifact_id}/notes")
def get_notes(
    artifact_id: uuid.UUID,
    kind: str = Query("notes"),
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Structured study notes (kind='notes') or a one-page cheat sheet (kind='cheatsheet').

    Generates in the background on first request. Returns
    {status: indexing|generating|ready|failed, kind, content}.
    """
    if kind not in NOTE_KINDS:
        kind = "notes"
    doc = _require_ready_doc(db, artifact_id, user, guest_id)
    if doc is None:
        return {"status": "indexing", "kind": kind, "content": ""}
    state = ensure_notes(db, artifact_id, kind)
    return {"status": state["status"], "kind": kind, "content": state["content"]}


@router.get("/{artifact_id}/flashcards")
def get_flashcards(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Active-recall flashcard deck. Generates in the background on first request.

    Returns {status: indexing|generating|ready|failed, cards: [{front,back,kind}]}.
    """
    doc = _require_ready_doc(db, artifact_id, user, guest_id)
    if doc is None:
        return {"status": "indexing", "cards": []}
    state = ensure_flashcards(db, artifact_id)
    return {"status": state["status"], "cards": state["cards"]}


@router.get("/{artifact_id}/memory-palace")
def get_memory_palace(
    artifact_id: uuid.UUID,
    setting: str = Query(""),
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """A memory-palace journey for the source (Magnetic Memory Method).

    Generates in the background on first request; passing a `setting` (a place the learner
    knows well) regenerates the journey there. Returns
    {status: indexing|generating|ready|failed, setting, palace: {setting,intro,stations:[…]}}.
    """
    doc = _require_ready_doc(db, artifact_id, user, guest_id)
    if doc is None:
        return {"status": "indexing", "setting": "", "palace": None}
    state = ensure_palace(db, artifact_id, setting=setting or None)
    return {"status": state["status"], "setting": state["setting"], "palace": state["palace"]}


# --------------------------------------------------- saved notes (Read / Study Buddy)
class SaveNoteIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    quote: str | None = Field(default=None, max_length=4000)


@router.get("/{artifact_id}/saved-notes")
def get_saved_notes(
    artifact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """All notes the learner saved while reading this source (newest first)."""
    _require_doc(db, artifact_id, user, guest_id)
    return {"notes": saved_notes_service.list_notes(db, artifact_id)}


@router.post("/{artifact_id}/saved-notes", dependencies=[Depends(require_csrf_or_guest)])
def create_saved_note(
    artifact_id: uuid.UUID,
    body: SaveNoteIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Save an answer (and the passage it came from) as a note linked to this source."""
    _require_doc(db, artifact_id, user, guest_id)
    return saved_notes_service.add_note(db, artifact_id, content=body.content, quote=body.quote)


@router.delete(
    "/{artifact_id}/saved-notes/{note_id}",
    status_code=204,
    dependencies=[Depends(require_csrf_or_guest)],
)
def delete_saved_note(
    artifact_id: uuid.UUID,
    note_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
):
    """Delete one saved note."""
    _require_doc(db, artifact_id, user, guest_id)
    saved_notes_service.delete_note(db, artifact_id, note_id)
