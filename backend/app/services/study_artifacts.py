"""Study-artifact storage + orchestration: notes, cheat sheets, flashcards.

Additive to MCQ/Explain. Each artifact is generated off the answer path by a worker and
cached per document; the read path (ensure_*) returns immediately, enqueuing generation
the first time. Raw parameterized SQL on qb.document_notes / qb.document_flashcards.

Both artifacts share their load/save/status/store skeleton via
``app.services.artifact_store`` — this module only declares each artifact's table
shape and supplies its generator + read-path invalidation rules.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.services.artifact_store import ArtifactStore, coerce_jsonb, run_artifact_generation

NOTE_KINDS = ("notes", "cheatsheet")

_NOTES_STORE = ArtifactStore(
    table="qb.document_notes",
    key_cols=["kind"],
    payload_col="content",
    cast_jsonb=False,  # content is TEXT, not JSONB
)
_FLASHCARDS_STORE = ArtifactStore(
    table="qb.document_flashcards",
    key_cols=[],
    payload_col="cards",
)


# --------------------------------------------------------------------------- notes
def load_notes(db: Session, document_id: uuid.UUID, kind: str) -> dict[str, Any]:
    """Return {status, content, error}. status: missing|generating|ready|failed."""
    row = _NOTES_STORE.load_row(db, document_id, kind=kind)
    if not row:
        return {"status": "missing", "content": "", "error": None}
    return {"status": row["status"], "content": row["content"] or "", "error": row["error"]}


def ensure_notes(db: Session, document_id: uuid.UUID, kind: str) -> dict[str, Any]:
    if kind not in NOTE_KINDS:
        kind = "notes"
    state = load_notes(db, document_id, kind)
    if state["status"] in ("ready", "generating"):
        return state
    from app.services.jobs import enqueue_notes

    _NOTES_STORE.set_status(db, document_id, "generating", kind=kind)
    db.commit()
    enqueue_notes(db, document_id, kind)
    return {"status": "generating", "content": "", "error": None}


def run_notes_generation(db: Session, document_id: uuid.UUID, kind: str) -> str:
    """Worker entry: build and persist study notes (or a cheat sheet) for a document."""
    from app.graphs.notes_graph import generate_notes

    if kind not in NOTE_KINDS:
        kind = "notes"
    return run_artifact_generation(
        db,
        document_id,
        store=_NOTES_STORE,
        generate=lambda: generate_notes(db, document_id, kind=kind),
        is_complete=lambda content: bool(content.strip()),
        empty_error="no_notes_generated",
        key_and_extra={"kind": kind},
    )


# ----------------------------------------------------------------------- flashcards
def load_flashcards(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    """Return {status, cards, error}. status: missing|generating|ready|failed."""
    row = _FLASHCARDS_STORE.load_row(db, document_id)
    if not row:
        return {"status": "missing", "cards": [], "error": None}
    return {"status": row["status"], "cards": coerce_jsonb(row["cards"]) or [], "error": row["error"]}


def ensure_flashcards(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    state = load_flashcards(db, document_id)
    if state["status"] in ("ready", "generating"):
        return state
    from app.services.jobs import enqueue_flashcards

    _FLASHCARDS_STORE.set_status(db, document_id, "generating")
    db.commit()
    enqueue_flashcards(db, document_id)
    return {"status": "generating", "cards": [], "error": None}


def run_flashcards_generation(db: Session, document_id: uuid.UUID) -> list[dict[str, str]]:
    """Worker entry: build and persist the flashcard deck for a document."""
    from app.graphs.flashcards_graph import generate_flashcards

    return run_artifact_generation(
        db,
        document_id,
        store=_FLASHCARDS_STORE,
        generate=lambda: generate_flashcards(db, document_id),
        is_complete=bool,
        empty_error="no_cards_generated",
    )

