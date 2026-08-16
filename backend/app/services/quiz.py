"""Quiz/worksheet storage + orchestration (Question Generator feature).

One generated quiz per document, regenerated when the requested config (types / count /
difficulty) changes. Generated off the answer path by a worker and cached. Raw
parameterized SQL on qb.document_quiz.

Persistence + worker skeleton are shared via ``app.services.artifact_store``; this
module owns the quiz-specific ``config`` column (a content-hash that invalidates the
cache when the requested question mix changes) and the read-path regenerate rule.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.engine_runtime import Pred, Rule, apply, first_match, pick
from app.graphs.quiz_graph import quiz_config_signature
from app.services.artifact_store import ArtifactStore, coerce_jsonb, run_artifact_generation
from app.services.source_fingerprint import is_artifact_stale, mark_artifact_fresh

# `config` is a content-hash that identifies a quiz variant; it's written on every
# upsert and compared on the read path so asking for a different mix regenerates.
_QUIZ_STORE = ArtifactStore(
    table="qb.document_quiz",
    key_cols=[],
    payload_col="questions",
    extra_cols={"config": "config"},
)


def load_quiz(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    """Return {status, config, questions, error}. status: missing|generating|ready|failed."""
    row = _QUIZ_STORE.load_row(db, document_id)
    return pick(
        not row,
        lambda: {"status": "missing", "config": "", "questions": [], "error": None},
        lambda: {
            "status": row["status"],
            "config": row["config"] or "",
            "questions": coerce_jsonb(row["questions"]) or [],
            "error": row["error"],
        },
    )


def ensure_quiz(
    db: Session, document_id: uuid.UUID, *, types: list[str], count: int, difficulty: str
) -> dict[str, Any]:
    """Read path: return the quiz, (re)generating in the background when the config changes."""
    config = quiz_config_signature(types, count, difficulty)
    state = load_quiz(db, document_id)
    fp_key = f"quiz:{config}"
    hit = first_match(
        (
            Rule(when=(Pred("ready_fresh", "truthy"),), action="keep"),
            Rule(when=(Pred("generating_same", "truthy"),), action="keep"),
            Rule(when=(), action="enqueue"),
        ),
        {
            "ready_fresh": (
                state["status"] == "ready"
                and state["config"] == config
                and not is_artifact_stale(db, document_id, fp_key)
            ),
            "generating_same": state["status"] == "generating" and state["config"] == config,
        },
    )

    def _enqueue() -> dict[str, Any]:
        from app.services.jobs import enqueue_quiz

        _QUIZ_STORE.set_status(db, document_id, "generating", config=config)
        db.commit()
        enqueue_quiz(db, document_id, types=types, count=count, difficulty=difficulty)
        return {"status": "generating", "config": config, "questions": [], "error": None}

    return apply(hit.action, {"keep": lambda: state, "enqueue": _enqueue})


def run_quiz_generation(
    db: Session, document_id: uuid.UUID, *, types: list[str], count: int, difficulty: str
) -> list[dict]:
    """Worker entry: build and persist the quiz for a document."""
    from app.graphs.quiz_graph import generate_quiz

    config = quiz_config_signature(types, count, difficulty)
    result = run_artifact_generation(
        db,
        document_id,
        store=_QUIZ_STORE,
        generate=lambda: generate_quiz(db, document_id, types=types, count=count, difficulty=difficulty),
        is_complete=bool,
        empty_error="no_questions_generated",
        key_and_extra={"config": config},
    )
    pick(
        bool(result),
        lambda: (mark_artifact_fresh(db, document_id, f"quiz:{config}"), db.commit()),
        lambda: None,
    )
    return result

