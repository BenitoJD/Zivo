"""Hydrate answered MCQ cards for step-back review (newspaper resume)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Account
from app.repositories.intel import concept_id
from app.services.answer_signal import ANSWER_CORRECT_METRIC_URI, resolve_subject_entity
from app.services.mcq_dedup import coerce_mcq_options, sanitize_mcq_stem
from app.services.question_pool import get_progress, learner_key_for


def _correct_indices(payload: dict[str, Any]) -> list[int] | None:
    raw = payload.get("correct_indices")
    if isinstance(raw, list) and len(raw) >= 2:
        return [int(x) for x in raw]
    raw_one = payload.get("correct_index")
    if isinstance(raw_one, list) and len(raw_one) >= 2:
        return [int(x) for x in raw_one]
    return None


def _correct_index(payload: dict[str, Any]) -> int:
    multi = _correct_indices(payload)
    if multi:
        return multi[0]
    raw = payload.get("correct_index")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, list) and raw:
        return int(raw[0])
    return 0


def build_learn_answered_review(
    db: Session,
    document_id: uuid.UUID,
    doc: Any,
    user: Account | None,
    guest_id: str | None,
) -> list[dict[str, Any]]:
    """Return answered questions in edition order for read-only review."""
    lk = learner_key_for(user, guest_id)
    progress = get_progress(doc, learner_key=lk)
    answered_ids = [str(x) for x in progress.get("answered_ids") or []]
    if not answered_ids:
        return []

    subject = resolve_subject_entity(db, user, guest_id)
    if not subject:
        return []

    metric_id = concept_id(db, ANSWER_CORRECT_METRIC_URI)
    rows = db.execute(
        text(
            """
            SELECT a.id::text AS assertion_id, a.payload,
                   m.value_numeric AS correct_numeric,
                   m.value_json AS answer_json
            FROM intel.assertion a
            LEFT JOIN intel.measurement m
              ON m.source_assertion_id = a.id
             AND m.subject_entity_id = :entity
             AND m.metric_concept_id = :metric
            WHERE a.id = ANY(CAST(:ids AS uuid[]))
              AND a.status = 'active'
              AND a.payload->>'artifact_id' = :artifact_id
            """
        ),
        {
            "ids": answered_ids,
            "entity": subject,
            "metric": metric_id,
            "artifact_id": str(document_id),
        },
    ).mappings().all()
    by_id = {str(r["assertion_id"]): r for r in rows}

    items: list[dict[str, Any]] = []
    for aid in answered_ids:
        row = by_id.get(aid)
        if not row:
            continue
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            continue

        stem = sanitize_mcq_stem(str(payload.get("question") or payload.get("stem") or ""))
        options = coerce_mcq_options(payload.get("options") or payload.get("choices"))
        if not stem or not options:
            continue

        answer_json = row["answer_json"]
        if isinstance(answer_json, str):
            answer_json = json.loads(answer_json)
        if not isinstance(answer_json, dict):
            answer_json = {}

        choice_index = int(answer_json.get("choice_index", 0))
        correct = bool(int(row["correct_numeric"] or 0))
        correct_indices = _correct_indices(payload)
        correct_index = correct_indices[0] if correct_indices else _correct_index(payload)

        option_feedback = payload.get("option_feedback")
        feedback = ""
        if isinstance(option_feedback, dict):
            feedback = str(option_feedback.get(str(choice_index)) or "").strip()
        if not feedback:
            feedback = str(payload.get("explanation") or "").strip()

        concept = payload.get("primary_concept") or payload.get("primary_concept_key")

        items.append(
            {
                "assertion_id": aid,
                "stem": stem,
                "options": options,
                "selected_index": choice_index,
                "selected_indices": answer_json.get("choice_indices"),
                "correct": correct,
                "correct_index": correct_index,
                "correct_indices": correct_indices,
                "feedback": feedback or None,
                "concept": concept,
                "first_try_correct": correct,
            }
        )
    return items
