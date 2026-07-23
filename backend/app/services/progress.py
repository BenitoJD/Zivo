"""Learner progress rollups from immutable answer measurements + tutor chat.

Extends the per-range study report (`/api/artifacts/{id}/report`) with lifetime,
per-source, concept, and recent-day views — same first-attempt signal, no
parallel telemetry store.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.repositories.intel import concept_id
from app.services.answer_signal import ANSWER_CORRECT_METRIC_URI


def _empty_progress() -> dict[str, Any]:
    return {
        "first_attempt_only": True,
        "answers": {"total": 0, "correct": 0, "wrong": 0, "accuracy": None},
        "today": {"answered": 0, "correct": 0, "questions_asked": 0},
        "questions_asked": 0,
        "recent": [],
        "sources": [],
        "topics": [],
    }


def _questions_asked_sql(
    *,
    account_id: uuid.UUID | None,
    guest_id: str | None,
    artifact_id: uuid.UUID | None,
    today_only: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Count user tutor messages for this learner (account or guest-scoped surface)."""
    params: dict[str, Any] = {}
    clauses = ["m.role = 'user'"]
    if today_only:
        clauses.append(
            "(m.created_at AT TIME ZONE 'UTC')::date = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date"
        )
    if account_id is not None:
        clauses.append("t.account_id = :account_id")
        params["account_id"] = account_id
    elif guest_id:
        # Guest threads encode isolation as surface `g:{guest_id}:{mode}`.
        clauses.append("t.account_id IS NULL")
        clauses.append("t.surface LIKE :guest_surface")
        params["guest_surface"] = f"g:{guest_id}:%"
    else:
        return "SELECT 0::int AS n", {}

    if artifact_id is not None:
        clauses.append("t.artifact_id = :artifact_id")
        params["artifact_id"] = artifact_id

    sql = f"""
        SELECT COUNT(*)::int AS n
        FROM qb.chat_message m
        JOIN qb.chat_thread t ON t.id = m.thread_id
        WHERE {" AND ".join(clauses)}
    """
    return sql, params


def _questions_asked_by_day_sql(
    *,
    account_id: uuid.UUID | None,
    guest_id: str | None,
    artifact_id: uuid.UUID | None,
    days: int,
) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {"days": days}
    clauses = [
        "m.role = 'user'",
        "m.created_at >= (CURRENT_DATE - (:days - 1))",
    ]
    if account_id is not None:
        clauses.append("t.account_id = :account_id")
        params["account_id"] = account_id
    elif guest_id:
        clauses.append("t.account_id IS NULL")
        clauses.append("t.surface LIKE :guest_surface")
        params["guest_surface"] = f"g:{guest_id}:%"
    else:
        return (
            """
            SELECT NULL::date AS day, 0::int AS questions_asked
            WHERE false
            """,
            {},
        )

    if artifact_id is not None:
        clauses.append("t.artifact_id = :artifact_id")
        params["artifact_id"] = artifact_id

    sql = f"""
        SELECT (m.created_at AT TIME ZONE 'UTC')::date AS day,
               COUNT(*)::int AS questions_asked
        FROM qb.chat_message m
        JOIN qb.chat_thread t ON t.id = m.thread_id
        WHERE {" AND ".join(clauses)}
        GROUP BY 1
    """
    return sql, params


def build_learner_progress(
    db: Session,
    *,
    subject_entity_id: uuid.UUID | None,
    account_id: uuid.UUID | None,
    guest_id: str | None,
    artifact_id: uuid.UUID | None = None,
    recent_days: int = 14,
) -> dict[str, Any]:
    """Aggregate first-attempt answers + tutor questions for one learner.

    ``artifact_id`` scopes every section to one source when set; otherwise
    returns the lifetime journal across sources the learner has answered.
    """
    if subject_entity_id is None and account_id is None and not guest_id:
        return _empty_progress()

    metric = concept_id(db, ANSWER_CORRECT_METRIC_URI)
    days = max(1, min(int(recent_days), 90))
    artifact_filter = ""
    params: dict[str, Any] = {
        "subject": subject_entity_id,
        "metric": metric,
        "days": days,
    }
    if artifact_id is not None:
        artifact_filter = "AND a.payload->>'artifact_id' = :artifact_id"
        params["artifact_id"] = str(artifact_id)

    answers = {"total": 0, "correct": 0, "wrong": 0, "accuracy": None}
    today = {"answered": 0, "correct": 0, "questions_asked": 0}
    topics: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    recent_map: dict[str, dict[str, int]] = {}

    if subject_entity_id is not None:
        totals = db.execute(
            text(
                f"""
                SELECT COUNT(*)::int AS total,
                       COALESCE(SUM(m.value_numeric), 0)::int AS correct
                FROM intel.measurement m
                JOIN intel.assertion a ON a.id = m.source_assertion_id
                WHERE m.subject_entity_id = :subject
                  AND m.metric_concept_id = :metric
                  {artifact_filter}
                """
            ),
            params,
        ).mappings().one()
        total = int(totals["total"] or 0)
        correct = int(totals["correct"] or 0)
        answers = {
            "total": total,
            "correct": correct,
            "wrong": total - correct,
            "accuracy": round(correct / total, 4) if total else None,
        }

        today_row = db.execute(
            text(
                f"""
                SELECT COUNT(*)::int AS answered,
                       COALESCE(SUM(m.value_numeric), 0)::int AS correct
                FROM intel.measurement m
                JOIN intel.assertion a ON a.id = m.source_assertion_id
                WHERE m.subject_entity_id = :subject
                  AND m.metric_concept_id = :metric
                  AND (m.observed_at AT TIME ZONE 'UTC')::date = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date
                  {artifact_filter}
                """
            ),
            params,
        ).mappings().one()
        today["answered"] = int(today_row["answered"] or 0)
        today["correct"] = int(today_row["correct"] or 0)

        topic_rows = db.execute(
            text(
                f"""
                SELECT COALESCE(NULLIF(TRIM(a.payload->>'primary_concept'), ''), 'General') AS concept,
                       COUNT(*)::int AS total,
                       COALESCE(SUM(m.value_numeric), 0)::int AS correct
                FROM intel.measurement m
                JOIN intel.assertion a ON a.id = m.source_assertion_id
                WHERE m.subject_entity_id = :subject
                  AND m.metric_concept_id = :metric
                  {artifact_filter}
                GROUP BY 1
                ORDER BY (COALESCE(SUM(m.value_numeric), 0)::float / NULLIF(COUNT(*), 0)) ASC,
                         COUNT(*) DESC
                LIMIT 24
                """
            ),
            params,
        ).mappings().all()
        topics = [
            {
                "concept": r["concept"],
                "correct": int(r["correct"]),
                "total": int(r["total"]),
            }
            for r in topic_rows
        ]

        source_rows = db.execute(
            text(
                f"""
                SELECT a.payload->>'artifact_id' AS artifact_id,
                       COALESCE(NULLIF(TRIM(d.filename), ''), 'Source') AS title,
                       COUNT(*)::int AS total,
                       COALESCE(SUM(m.value_numeric), 0)::int AS correct,
                       MAX(m.observed_at) AS last_answered_at
                FROM intel.measurement m
                JOIN intel.assertion a ON a.id = m.source_assertion_id
                LEFT JOIN qb.documents d ON d.id::text = a.payload->>'artifact_id'
                WHERE m.subject_entity_id = :subject
                  AND m.metric_concept_id = :metric
                  {artifact_filter}
                GROUP BY 1, 2
                ORDER BY MAX(m.observed_at) DESC NULLS LAST
                """
            ),
            params,
        ).mappings().all()
        sources = [
            {
                "artifact_id": r["artifact_id"],
                "title": r["title"],
                "total": int(r["total"]),
                "correct": int(r["correct"]),
                "wrong": int(r["total"]) - int(r["correct"]),
                "last_answered_at": (
                    r["last_answered_at"].isoformat() if r["last_answered_at"] else None
                ),
            }
            for r in source_rows
            if r["artifact_id"]
        ]

        day_rows = db.execute(
            text(
                f"""
                SELECT (m.observed_at AT TIME ZONE 'UTC')::date AS day,
                       COUNT(*)::int AS answered,
                       COALESCE(SUM(m.value_numeric), 0)::int AS correct
                FROM intel.measurement m
                JOIN intel.assertion a ON a.id = m.source_assertion_id
                WHERE m.subject_entity_id = :subject
                  AND m.metric_concept_id = :metric
                  AND m.observed_at >= (CURRENT_DATE - (:days - 1))
                  {artifact_filter}
                GROUP BY 1
                ORDER BY 1 ASC
                """
            ),
            params,
        ).mappings().all()
        for r in day_rows:
            key = r["day"].isoformat()
            recent_map[key] = {
                "answered": int(r["answered"]),
                "correct": int(r["correct"]),
                "questions_asked": 0,
            }

    q_sql, q_params = _questions_asked_sql(
        account_id=account_id, guest_id=guest_id, artifact_id=artifact_id
    )
    questions_asked = int(db.execute(text(q_sql), q_params).scalar() or 0)

    today_q_sql, today_q_params = _questions_asked_sql(
        account_id=account_id,
        guest_id=guest_id,
        artifact_id=artifact_id,
        today_only=True,
    )
    today["questions_asked"] = int(db.execute(text(today_q_sql), today_q_params).scalar() or 0)

    chat_day_sql, chat_day_params = _questions_asked_by_day_sql(
        account_id=account_id,
        guest_id=guest_id,
        artifact_id=artifact_id,
        days=days,
    )
    for r in db.execute(text(chat_day_sql), chat_day_params).mappings().all():
        if not r["day"]:
            continue
        key = r["day"].isoformat()
        bucket = recent_map.setdefault(
            key, {"answered": 0, "correct": 0, "questions_asked": 0}
        )
        bucket["questions_asked"] = int(r["questions_asked"])

    # Attach per-source question counts when listing sources.
    if sources and (account_id is not None or guest_id):
        source_ids = [s["artifact_id"] for s in sources if s.get("artifact_id")]
        if source_ids:
            chat_params: dict[str, Any] = {"ids": source_ids}
            chat_clauses = [
                "m.role = 'user'",
                "t.artifact_id::text = ANY(:ids)",
            ]
            if account_id is not None:
                chat_clauses.append("t.account_id = :account_id")
                chat_params["account_id"] = account_id
            else:
                chat_clauses.append("t.account_id IS NULL")
                chat_clauses.append("t.surface LIKE :guest_surface")
                chat_params["guest_surface"] = f"g:{guest_id}:%"
            chat_by_source = db.execute(
                text(
                    f"""
                    SELECT t.artifact_id::text AS artifact_id,
                           COUNT(*)::int AS questions_asked
                    FROM qb.chat_message m
                    JOIN qb.chat_thread t ON t.id = m.thread_id
                    WHERE {" AND ".join(chat_clauses)}
                    GROUP BY 1
                    """
                ),
                chat_params,
            ).mappings().all()
            asked_map = {r["artifact_id"]: int(r["questions_asked"]) for r in chat_by_source}
            for s in sources:
                s["questions_asked"] = asked_map.get(s["artifact_id"], 0)
        else:
            for s in sources:
                s["questions_asked"] = 0
    else:
        for s in sources:
            s.setdefault("questions_asked", 0)

    recent = [
        {
            "day": day,
            "answered": vals["answered"],
            "correct": vals["correct"],
            "questions_asked": vals["questions_asked"],
        }
        for day, vals in sorted(recent_map.items())
        if vals["answered"] or vals["questions_asked"]
    ]

    return {
        "first_attempt_only": True,
        "answers": answers,
        "today": today,
        "questions_asked": questions_asked,
        "recent": recent,
        "sources": sources,
        "topics": topics,
        "scoped_artifact_id": str(artifact_id) if artifact_id else None,
    }
