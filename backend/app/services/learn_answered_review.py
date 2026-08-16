"""Hydrate answered MCQ cards for step-back review (newspaper resume)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import Pred, Rule, apply, first_match, pick
from app.models import Account
from app.repositories.intel import concept_id
from app.services.answer_signal import ANSWER_CORRECT_METRIC_URI, resolve_subject_entity
from app.services.mcq_dedup import coerce_mcq_options, sanitize_mcq_stem
from app.services.question_pool import get_progress, learner_key_for


def _correct_indices(payload: dict[str, Any]) -> list[int] | None:
    raw = payload.get("correct_indices")
    raw_one = payload.get("correct_index")
    rule = first_match(
        [
            Rule(when=(Pred("multi", "truthy"),), action="raw"),
            Rule(when=(Pred("one", "truthy"),), action="one"),
        ],
        {
            "multi": isinstance(raw, list) and len(raw) >= 2,
            "one": isinstance(raw_one, list) and len(raw_one) >= 2,
        },
        default=Rule(when=(), action="none"),
    )
    return apply(
        rule.action,
        {
            "raw": lambda: [int(x) for x in raw],
            "one": lambda: [int(x) for x in raw_one],
            "none": lambda: None,
        },
    )


def _correct_index(payload: dict[str, Any]) -> int:
    multi = _correct_indices(payload)
    raw = payload.get("correct_index")
    rule = first_match(
        [
            Rule(when=(Pred("multi", "truthy"),), action="multi0"),
            Rule(when=(Pred("is_int", "truthy"),), action="int"),
            Rule(when=(Pred("list_raw", "truthy"),), action="list0"),
        ],
        {
            "multi": multi,
            "is_int": isinstance(raw, int),
            "list_raw": isinstance(raw, list) and bool(raw),
        },
        default=Rule(when=(), action="zero"),
    )
    return apply(
        rule.action,
        {
            "multi0": lambda: multi[0],
            "int": lambda: raw,
            "list0": lambda: int(raw[0]),
            "zero": lambda: 0,
        },
    )


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

    def _empty() -> list[dict[str, Any]]:
        return []

    def _build() -> list[dict[str, Any]]:
        subject = resolve_subject_entity(db, user, guest_id)

        def _hydrate() -> list[dict[str, Any]]:
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

                def _append() -> None:
                    payload = row["payload"]
                    payload = pick(
                        isinstance(payload, str),
                        lambda: json.loads(payload),
                        lambda: payload,
                    )

                    def _from_dict() -> None:
                        stem = sanitize_mcq_stem(
                            str(payload.get("question") or payload.get("stem") or "")
                        )
                        options = coerce_mcq_options(
                            payload.get("options") or payload.get("choices")
                        )

                        def _card() -> None:
                            answer_json = row["answer_json"]
                            answer_json = pick(
                                isinstance(answer_json, str),
                                lambda: json.loads(answer_json),
                                lambda: answer_json,
                            )
                            answer_json = pick(
                                isinstance(answer_json, dict),
                                lambda: answer_json,
                                lambda: {},
                            )
                            choice_index = int(answer_json.get("choice_index", 0))
                            correct = bool(int(row["correct_numeric"] or 0))
                            correct_indices = _correct_indices(payload)
                            correct_index = pick(
                                bool(correct_indices),
                                lambda: correct_indices[0],
                                lambda: _correct_index(payload),
                            )
                            option_feedback = payload.get("option_feedback")
                            feedback = pick(
                                isinstance(option_feedback, dict),
                                lambda: str(option_feedback.get(str(choice_index)) or "").strip(),
                                lambda: "",
                            )
                            feedback = pick(
                                bool(feedback),
                                lambda: feedback,
                                lambda: str(payload.get("explanation") or "").strip(),
                            )
                            concept = payload.get("primary_concept") or payload.get(
                                "primary_concept_key"
                            )
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

                        pick(not stem or not options, lambda: None, _card)

                    pick(isinstance(payload, dict), _from_dict, lambda: None)

                pick(not row, lambda: None, _append)
            return items

        return pick(not subject, _empty, _hydrate)

    return pick(not answered_ids, _empty, _build)
