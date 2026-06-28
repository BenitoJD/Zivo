"""Study artifacts — notes, cheat sheets, flashcards (Scribely-style, additive to MCQ)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Account
from app.services import saved_notes as saved_notes_service
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.guest_session import guest_session_for_read
from app.api.access import require_document, require_ready_document
from app.services.memory_palace import ensure_palace
from app.services.quiz import ensure_quiz, load_quiz
from app.services.study_artifacts import NOTE_KINDS, ensure_flashcards, ensure_notes

router = APIRouter()




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
    doc = require_ready_document(db, artifact_id, user, guest_id)
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
    doc = require_ready_document(db, artifact_id, user, guest_id)
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
    doc = require_ready_document(db, artifact_id, user, guest_id)
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
    require_document(db, artifact_id, user, guest_id)
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
    require_document(db, artifact_id, user, guest_id)
    return saved_notes_service.add_note(db, artifact_id, content=body.content, quote=body.quote)


def _parse_types(types: str) -> list[str]:
    return [t.strip() for t in (types or "").split(",") if t.strip()]


@router.get("/{artifact_id}/quiz")
def get_quiz(
    artifact_id: uuid.UUID,
    types: str = Query("mcq"),
    count: int = Query(10),
    difficulty: str = Query("mixed"),
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Generate a quiz/worksheet of the chosen question types (Question Generator).

    Regenerates in the background when the config (types/count/difficulty) changes.
    Returns {status: indexing|generating|ready|failed, config, questions:[…]} with the
    answer key included (the client hides answers for the student view).
    """
    doc = require_ready_document(db, artifact_id, user, guest_id)
    if doc is None:
        return {"status": "indexing", "config": "", "questions": []}
    state = ensure_quiz(db, artifact_id, types=_parse_types(types), count=count, difficulty=difficulty)
    return {"status": state["status"], "config": state["config"], "questions": state["questions"]}


@router.get("/{artifact_id}/quiz/export.docx")
def export_quiz_docx(
    artifact_id: uuid.UUID,
    answers: int = Query(0),
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
):
    """Download the generated quiz as a .docx worksheet (answers=1 → teacher answer key)."""
    doc = require_document(db, artifact_id, user, guest_id)
    state = load_quiz(db, artifact_id)
    if state["status"] != "ready" or not state["questions"]:
        raise HTTPException(status_code=409, detail="Quiz not ready")

    from app.services.quiz_export import build_quiz_docx

    title = (doc.filename or "Quiz").rsplit(".", 1)[0]
    data = build_quiz_docx(title=title, questions=state["questions"], with_answers=bool(answers))
    suffix = "answer-key" if answers else "worksheet"
    fname = f"{title[:60]}-{suffix}.docx".replace('"', "")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


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
    require_document(db, artifact_id, user, guest_id)
    saved_notes_service.delete_note(db, artifact_id, note_id)
