"""Memory Palace storage + orchestration (Magnetic Memory Method).

Additive to MCQ/Explain/Notes. One palace per document, generated off the answer path by
a worker and cached. The read path (ensure_palace) returns immediately, enqueuing generation
the first time — and regenerating when the learner picks a different place
(``setting``) so they can anchor the journey somewhere they personally know well.

Persistence + worker skeleton are shared via ``app.services.artifact_store``; this
module owns the palace-specific ``setting`` column (which drives regeneration) and
the read-path setting-match rule that ``ensure_palace`` implements by hand.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.engine_runtime import Pred, Rule, apply, first_match, pick
from app.services.artifact_store import ArtifactStore, coerce_jsonb, run_artifact_generation
from app.services.source_fingerprint import is_artifact_stale, mark_artifact_fresh

# `setting` is the learner's chosen place-name; it's written on every upsert and
# compared on the read path so a new place regenerates the journey.
_PALACE_STORE = ArtifactStore(
    table="qb.document_memory_palace",
    key_cols=[],
    payload_col="palace",
    extra_cols={"setting": "setting"},
)

_ENSURE_RULES = (
    Rule(when=(Pred("ready_match", "truthy"),), action="keep"),
    Rule(when=(Pred("generating_match", "truthy"),), action="keep"),
    Rule(when=(), action="kick"),
)


def load_palace(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    """Return {status, setting, palace, error}. status: missing|generating|ready|failed."""
    row = _PALACE_STORE.load_row(db, document_id)
    return pick(
        not row,
        lambda: {"status": "missing", "setting": "", "palace": None, "error": None},
        lambda: {
            "status": row["status"],
            "setting": row["setting"] or "",
            "palace": coerce_jsonb(row["palace"]) or None,
            "error": row["error"],
        },
    )


def ensure_palace(
    db: Session, document_id: uuid.UUID, *, setting: str | None = None
) -> dict[str, Any]:
    """Read path: return the palace, (re)generating in the background when needed.

    No setting → build once (AI picks a familiar place). An explicit setting that differs
    from the stored one → regenerate the journey in that place.
    """
    state = load_palace(db, document_id)
    requested = (setting or "").strip()
    stored = (state["setting"] or "").strip().lower()
    fp_key = f"palace:{(requested or state.get('setting') or '').strip().lower()}"
    same = stored == requested.lower()
    hit = first_match(
        _ENSURE_RULES,
        {
            "ready_match": (
                state["status"] == "ready"
                and pick(bool(requested), lambda: same, lambda: True)
                and not is_artifact_stale(db, document_id, fp_key)
            ),
            "generating_match": state["status"] == "generating"
            and pick(bool(requested), lambda: same, lambda: True),
        },
    )
    return apply(
        hit.action,
        {
            "keep": lambda: state,
            "kick": lambda: _kick(db, document_id, requested),
        },
    )


def _kick(db: Session, document_id: uuid.UUID, setting: str) -> dict[str, Any]:
    from app.services.jobs import enqueue_memory_palace

    _PALACE_STORE.set_status(db, document_id, "generating", setting=setting)
    db.commit()
    enqueue_memory_palace(db, document_id, setting)
    return {"status": "generating", "setting": setting, "palace": None, "error": None}


def run_palace_generation(db: Session, document_id: uuid.UUID, setting: str = "") -> dict:
    """Worker entry: build and persist the memory palace for a document."""
    from app.graphs.memory_palace_graph import generate_memory_palace

    result = run_artifact_generation(
        db,
        document_id,
        store=_PALACE_STORE,
        generate=lambda: generate_memory_palace(db, document_id, setting=setting),
        is_complete=lambda palace: bool(palace and palace.get("stations")),
        empty_error="no_palace_generated",
        key_and_extra={"setting": setting},
    )
    pick(
        bool(result and result.get("stations")),
        lambda: (mark_artifact_fresh(db, document_id, f"palace:{(setting or '').strip().lower()}"), db.commit()),
        lambda: None,
    )
    return result
