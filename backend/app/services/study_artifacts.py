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

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.services.artifact_store import ArtifactStore, coerce_jsonb, run_artifact_generation
from app.services.source_fingerprint import is_artifact_stale, mark_artifact_fresh

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

_ENSURE_RULES = (
    Rule(when=(Pred("ready_fresh", "truthy"),), action="keep"),
    Rule(when=(Pred("generating", "truthy"),), action="keep"),
    Rule(when=(), action="enqueue"),
)


def load_notes(db: Session, document_id: uuid.UUID, kind: str) -> dict[str, Any]:
    """Return {status, content, error}. status: missing|generating|ready|failed."""
    row = _NOTES_STORE.load_row(db, document_id, kind=kind)
    return pick(
        not row,
        lambda: {"status": "missing", "content": "", "error": None},
        lambda: {"status": row["status"], "content": row["content"] or "", "error": row["error"]},
    )


def ensure_notes(db: Session, document_id: uuid.UUID, kind: str) -> dict[str, Any]:
    kind = choose(kind in NOTE_KINDS, kind, "notes")
    state = load_notes(db, document_id, kind)
    fp_key = f"notes:{kind}"
    hit = first_match(
        _ENSURE_RULES,
        {
            "ready_fresh": state["status"] == "ready" and not is_artifact_stale(db, document_id, fp_key),
            "generating": state["status"] == "generating",
        },
    )

    def _enqueue() -> dict[str, Any]:
        from app.services.jobs import enqueue_notes

        _NOTES_STORE.set_status(db, document_id, "generating", kind=kind)
        db.commit()
        enqueue_notes(db, document_id, kind)
        return {"status": "generating", "content": "", "error": None}

    return apply(hit.action, {"keep": lambda: state, "enqueue": _enqueue})


def run_notes_generation(db: Session, document_id: uuid.UUID, kind: str) -> str:
    """Worker entry: build and persist study notes (or a cheat sheet) for a document."""
    from app.graphs.notes_graph import generate_notes

    kind = choose(kind in NOTE_KINDS, kind, "notes")
    result = run_artifact_generation(
        db,
        document_id,
        store=_NOTES_STORE,
        generate=lambda: generate_notes(db, document_id, kind=kind),
        is_complete=lambda content: bool(content.strip()),
        empty_error="no_notes_generated",
        key_and_extra={"kind": kind},
    )
    pick(
        isinstance(result, str) and bool(result.strip()),
        lambda: (mark_artifact_fresh(db, document_id, f"notes:{kind}"), db.commit()),
        lambda: None,
    )
    return result


def load_flashcards(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    """Return {status, cards, error}. status: missing|generating|ready|failed."""
    row = _FLASHCARDS_STORE.load_row(db, document_id)
    return pick(
        not row,
        lambda: {"status": "missing", "cards": [], "error": None},
        lambda: {"status": row["status"], "cards": coerce_jsonb(row["cards"]) or [], "error": row["error"]},
    )


def ensure_flashcards(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    state = load_flashcards(db, document_id)
    hit = first_match(
        _ENSURE_RULES,
        {
            "ready_fresh": state["status"] == "ready" and not is_artifact_stale(db, document_id, "flashcards"),
            "generating": state["status"] == "generating",
        },
    )

    def _enqueue() -> dict[str, Any]:
        from app.services.jobs import enqueue_flashcards

        _FLASHCARDS_STORE.set_status(db, document_id, "generating")
        db.commit()
        enqueue_flashcards(db, document_id)
        return {"status": "generating", "cards": [], "error": None}

    return apply(hit.action, {"keep": lambda: state, "enqueue": _enqueue})


def run_flashcards_generation(db: Session, document_id: uuid.UUID) -> list[dict[str, str]]:
    """Worker entry: build and persist the flashcard deck for a document."""
    from app.graphs.flashcards_graph import generate_flashcards

    result = run_artifact_generation(
        db,
        document_id,
        store=_FLASHCARDS_STORE,
        generate=lambda: generate_flashcards(db, document_id),
        is_complete=bool,
        empty_error="no_cards_generated",
    )
    pick(
        bool(result),
        lambda: (mark_artifact_fresh(db, document_id, "flashcards"), db.commit()),
        lambda: None,
    )
    return result
