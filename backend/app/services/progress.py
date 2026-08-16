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

from app.engine_runtime import apply, choose, pick
from app.repositories.intel import concept_id
from app.services.answer_signal import ANSWER_CORRECT_METRIC_URI
from app.services.mastery_evidence import (
    plan_progress_recent_days,
    plan_progress_topic_display_limit,
)
from app.services.mcq_dedup import short_concept_label
from app.services.session_design import evaluate_learner_identity


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


def _identity_clauses(
    *,
    account_id: uuid.UUID | None,
    guest_id: str | None,
) -> tuple[list[str], dict[str, Any]]:
    return apply(
        evaluate_learner_identity(account_id=account_id, guest_id=guest_id),
        {
            "account": lambda: (["t.account_id = :account_id"], {"account_id": account_id}),
            "guest": lambda: (
                ["t.account_id IS NULL", "t.surface LIKE :guest_surface"],
                {"guest_surface": f"g:{guest_id}:%"},
            ),
            "none": lambda: ([], {}),
        },
    )


def _questions_asked_sql(
    *,
    account_id: uuid.UUID | None,
    guest_id: str | None,
    artifact_id: uuid.UUID | None,
    today_only: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Count user tutor messages for this learner (account or guest-scoped surface)."""
    identity = evaluate_learner_identity(account_id=account_id, guest_id=guest_id)
    return pick(
        identity == "none",
        lambda: ("SELECT 0::int AS n", {}),
        lambda: _questions_asked_identity_sql(
            account_id=account_id,
            guest_id=guest_id,
            artifact_id=artifact_id,
            today_only=today_only,
        ),
    )


def _questions_asked_identity_sql(
    *,
    account_id: uuid.UUID | None,
    guest_id: str | None,
    artifact_id: uuid.UUID | None,
    today_only: bool,
) -> tuple[str, dict[str, Any]]:
    clauses = ["m.role = 'user'"]
    extra, params = _identity_clauses(account_id=account_id, guest_id=guest_id)
    clauses.extend(extra)
    pick(
        today_only,
        lambda: clauses.append(
            "(m.created_at AT TIME ZONE 'UTC')::date = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date"
        ),
        lambda: None,
    )
    pick(
        artifact_id is not None,
        lambda: (clauses.append("t.artifact_id = :artifact_id"), params.__setitem__("artifact_id", artifact_id)),
        lambda: None,
    )
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
    identity = evaluate_learner_identity(account_id=account_id, guest_id=guest_id)
    return pick(
        identity == "none",
        lambda: (
            """
            SELECT NULL::date AS day, 0::int AS questions_asked
            WHERE false
            """,
            {},
        ),
        lambda: _questions_asked_by_day_identity_sql(
            account_id=account_id,
            guest_id=guest_id,
            artifact_id=artifact_id,
            days=days,
        ),
    )


def _questions_asked_by_day_identity_sql(
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
    extra, extra_params = _identity_clauses(account_id=account_id, guest_id=guest_id)
    clauses.extend(extra)
    params.update(extra_params)
    pick(
        artifact_id is not None,
        lambda: (clauses.append("t.artifact_id = :artifact_id"), params.__setitem__("artifact_id", artifact_id)),
        lambda: None,
    )
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
    recent_days: int | None = None,
) -> dict[str, Any]:
    """Aggregate first-attempt answers + tutor questions for one learner.

    ``artifact_id`` scopes every section to one source when set; otherwise
    returns the lifetime journal across sources the learner has answered.
    """
    return pick(
        subject_entity_id is None and account_id is None and not guest_id,
        _empty_progress,
        lambda: _build_progress(
            db,
            subject_entity_id=subject_entity_id,
            account_id=account_id,
            guest_id=guest_id,
            artifact_id=artifact_id,
            recent_days=recent_days,
        ),
    )


def _build_progress(
    db: Session,
    *,
    subject_entity_id: uuid.UUID | None,
    account_id: uuid.UUID | None,
    guest_id: str | None,
    artifact_id: uuid.UUID | None,
    recent_days: int | None,
) -> dict[str, Any]:
    metric = concept_id(db, ANSWER_CORRECT_METRIC_URI)
    days = plan_progress_recent_days(recent_days)
    params: dict[str, Any] = {
        "subject": subject_entity_id,
        "metric": metric,
        "days": days,
    }
    artifact_filter = pick(
        artifact_id is not None,
        lambda: _with_artifact(params, artifact_id),
        lambda: "",
    )

    answers = {"total": 0, "correct": 0, "wrong": 0, "accuracy": None}
    today = {"answered": 0, "correct": 0, "questions_asked": 0}
    topics: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    recent_map: dict[str, dict[str, int]] = {}

    pick(
        subject_entity_id is not None,
        lambda: _fill_subject_sections(
            db,
            params=params,
            artifact_filter=artifact_filter,
            answers=answers,
            today=today,
            topics=topics,
            sources=sources,
            recent_map=recent_map,
        ),
        lambda: None,
    )

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
        _maybe_add_chat_day(recent_map, r)

    _attach_source_questions(db, sources, account_id=account_id, guest_id=guest_id)

    recent = list(
        filter(
            lambda row: row["answered"] or row["questions_asked"],
            (
                {
                    "day": day,
                    "answered": vals["answered"],
                    "correct": vals["correct"],
                    "questions_asked": vals["questions_asked"],
                }
                for day, vals in sorted(recent_map.items())
            ),
        )
    )

    return {
        "first_attempt_only": True,
        "answers": answers,
        "today": today,
        "questions_asked": questions_asked,
        "recent": recent,
        "sources": sources,
        "topics": topics,
        "scoped_artifact_id": pick(bool(artifact_id), lambda: str(artifact_id), lambda: None),
    }


def _with_artifact(params: dict[str, Any], artifact_id: uuid.UUID) -> str:
    params["artifact_id"] = str(artifact_id)
    return "AND a.payload->>'artifact_id' = :artifact_id"


def _fill_subject_sections(
    db: Session,
    *,
    params: dict[str, Any],
    artifact_filter: str,
    answers: dict[str, Any],
    today: dict[str, Any],
    topics: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    recent_map: dict[str, dict[str, int]],
) -> None:
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
    answers.update(
        {
            "total": total,
            "correct": correct,
            "wrong": total - correct,
            "accuracy": choose(bool(total), round(correct / total, 4), None),
        }
    )

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
            LIMIT :lim
            """
        ),
        {**params, "lim": plan_progress_topic_display_limit()},
    ).mappings().all()
    topics.extend(
        {
            "concept": short_concept_label(r["concept"]),
            "correct": int(r["correct"]),
            "total": int(r["total"]),
        }
        for r in topic_rows
    )

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
    sources.extend(
        map(
            _source_row,
            filter(lambda r: r["artifact_id"], source_rows),
        )
    )

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
        recent_map[r["day"].isoformat()] = {
            "answered": int(r["answered"]),
            "correct": int(r["correct"]),
            "questions_asked": 0,
        }


def _source_row(r: Any) -> dict[str, Any]:
    return {
        "artifact_id": r["artifact_id"],
        "title": r["title"],
        "total": int(r["total"]),
        "correct": int(r["correct"]),
        "wrong": int(r["total"]) - int(r["correct"]),
        "last_answered_at": pick(
            bool(r["last_answered_at"]),
            lambda: r["last_answered_at"].isoformat(),
            lambda: None,
        ),
    }


def _maybe_add_chat_day(recent_map: dict[str, dict[str, int]], r: Any) -> None:
    pick(not r["day"], lambda: None, lambda: _add_chat_day(recent_map, r))


def _add_chat_day(recent_map: dict[str, dict[str, int]], r: Any) -> None:
    key = r["day"].isoformat()
    bucket = recent_map.setdefault(key, {"answered": 0, "correct": 0, "questions_asked": 0})
    bucket["questions_asked"] = int(r["questions_asked"])


def _attach_source_questions(
    db: Session,
    sources: list[dict[str, Any]],
    *,
    account_id: uuid.UUID | None,
    guest_id: str | None,
) -> None:
    identity = evaluate_learner_identity(account_id=account_id, guest_id=guest_id)
    source_ids = list(filter(None, (s.get("artifact_id") for s in sources)))
    apply(
        choose(
            bool(sources) and identity != "none" and bool(source_ids),
            "query",
            choose(bool(sources) and identity != "none", "zero", "default"),
        ),
        {
            "query": lambda: _query_source_questions(
                db, sources, source_ids, account_id=account_id, guest_id=guest_id, identity=identity
            ),
            "zero": lambda: [_set_asked(s, 0) for s in sources],
            "default": lambda: [_default_asked(s) for s in sources],
        },
    )


def _set_asked(s: dict[str, Any], n: int) -> None:
    s["questions_asked"] = n


def _default_asked(s: dict[str, Any]) -> None:
    s.setdefault("questions_asked", 0)


def _query_source_questions(
    db: Session,
    sources: list[dict[str, Any]],
    source_ids: list[Any],
    *,
    account_id: uuid.UUID | None,
    guest_id: str | None,
    identity: str,
) -> None:
    chat_params: dict[str, Any] = {"ids": source_ids}
    extra, extra_params = _identity_clauses(account_id=account_id, guest_id=guest_id)
    chat_params.update(extra_params)
    chat_clauses = ["m.role = 'user'", "t.artifact_id::text = ANY(:ids)", *extra]
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
