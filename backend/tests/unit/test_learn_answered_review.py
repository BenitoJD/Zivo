"""Learn-answered review hydration."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.services.learn_answered_review import build_learn_answered_review


def test_build_learn_answered_review_orders_by_progress() -> None:
    doc = MagicMock()
    doc.id = uuid.uuid4()
    aid1 = str(uuid.uuid4())
    aid2 = str(uuid.uuid4())
    entity_id = uuid.uuid4()
    db = MagicMock()

    payload1 = {
        "question": "First?",
        "options": ["A", "B"],
        "correct_index": 1,
        "explanation": "Because B.",
    }
    payload2 = {
        "question": "Second?",
        "options": ["X", "Y"],
        "correct_index": 0,
        "option_feedback": {"1": "Not Y."},
    }

    def execute_side_effect(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        if "document_learner_state" in sql or "question_progress" in sql:
            pass
        if "FROM intel.assertion" in sql:
            result.mappings.return_value.all.return_value = [
                {
                    "assertion_id": aid2,
                    "payload": payload2,
                    "correct_numeric": 0,
                    "answer_json": {"choice_index": 1},
                },
                {
                    "assertion_id": aid1,
                    "payload": payload1,
                    "correct_numeric": 1,
                    "answer_json": {"choice_index": 1},
                },
            ]
        return result

    db.execute.side_effect = execute_side_effect

    with (
        patch("app.services.learn_answered_review.get_progress", return_value={"answered_ids": [aid1, aid2]}),
        patch("app.services.learn_answered_review.learner_key_for", return_value="guest:g1"),
        patch("app.services.learn_answered_review.resolve_subject_entity", return_value=entity_id),
        patch("app.services.learn_answered_review.concept_id", return_value=uuid.uuid4()),
    ):
        items = build_learn_answered_review(db, doc.id, doc, None, "g1")

    assert len(items) == 2
    assert items[0]["assertion_id"] == aid1
    assert items[0]["stem"] == "First?"
    assert items[0]["correct"] is True
    assert items[1]["assertion_id"] == aid2
    assert items[1]["feedback"] == "Not Y."
