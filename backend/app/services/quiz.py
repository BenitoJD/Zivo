"""Quiz/worksheet storage + orchestration (Question Generator feature).

One generated quiz per document, regenerated when the requested config (types / count /
difficulty) changes. Generated off the answer path by a worker and cached. Raw
parameterized SQL on qb.document_quiz.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.graphs.quiz_graph import quiz_config_signature


def load_quiz(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    """Return {status, config, questions, error}. status: missing|generating|ready|failed."""
    row = db.execute(
        text("SELECT config, questions, status, error FROM qb.document_quiz WHERE document_id = :id"),
        {"id": document_id},
    ).mappings().first()
    if not row:
        return {"status": "missing", "config": "", "questions": [], "error": None}
    qs = row["questions"]
    if isinstance(qs, str):
        qs = json.loads(qs)
    return {"status": row["status"], "config": row["config"] or "", "questions": qs or [], "error": row["error"]}


def _set_status(db: Session, document_id: uuid.UUID, status: str, config: str, *, error: str | None = None) -> None:
    db.execute(
        text(
            """
            INSERT INTO qb.document_quiz (document_id, config, status, error, updated_at)
            VALUES (:id, :config, :status, :error, now())
            ON CONFLICT (document_id)
            DO UPDATE SET config = EXCLUDED.config, status = EXCLUDED.status,
                          error = EXCLUDED.error, updated_at = now()
            """
        ),
        {"id": document_id, "config": config, "status": status, "error": error},
    )


def save_quiz(db: Session, document_id: uuid.UUID, config: str, questions: list[dict]) -> None:
    db.execute(
        text(
            """
            INSERT INTO qb.document_quiz (document_id, config, questions, status, error, updated_at)
            VALUES (:id, :config, CAST(:q AS jsonb), 'ready', NULL, now())
            ON CONFLICT (document_id)
            DO UPDATE SET config = EXCLUDED.config, questions = EXCLUDED.questions,
                          status = 'ready', error = NULL, updated_at = now()
            """
        ),
        {"id": document_id, "config": config, "q": json.dumps(questions)},
    )


def ensure_quiz(
    db: Session, document_id: uuid.UUID, *, types: list[str], count: int, difficulty: str
) -> dict[str, Any]:
    """Read path: return the quiz, (re)generating in the background when the config changes."""
    config = quiz_config_signature(types, count, difficulty)
    state = load_quiz(db, document_id)
    if state["status"] in ("ready", "generating") and state["config"] == config:
        return state
    from app.services.jobs import enqueue_quiz

    _set_status(db, document_id, "generating", config)
    db.commit()
    enqueue_quiz(db, document_id, types=types, count=count, difficulty=difficulty)
    return {"status": "generating", "config": config, "questions": [], "error": None}


def run_quiz_generation(
    db: Session, document_id: uuid.UUID, *, types: list[str], count: int, difficulty: str
) -> list[dict]:
    """Worker entry: build and persist the quiz for a document."""
    import asyncio

    from app.graphs.quiz_graph import generate_quiz

    config = quiz_config_signature(types, count, difficulty)
    _set_status(db, document_id, "generating", config)
    db.commit()
    try:
        questions = asyncio.run(generate_quiz(db, document_id, types=types, count=count, difficulty=difficulty))
    except Exception as exc:
        _set_status(db, document_id, "failed", config, error=str(exc)[:500])
        db.commit()
        raise
    if questions:
        save_quiz(db, document_id, config, questions)
    else:
        _set_status(db, document_id, "failed", config, error="no_questions_generated")
    db.commit()
    return questions
