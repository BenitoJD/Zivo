"""Unit tests for rolling question pool."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.services.question_pool import (
    REFILL_AFTER_ANSWERED,
    clear_stale_generation_pending,
    ensure_question_pool,
    get_progress,
    get_question_budget,
    maybe_refill_pool,
    record_answer,
)


def test_get_progress_defaults_to_selected_range_first_page() -> None:
    doc = MagicMock()
    doc.meta = {"selected_range": {"from": 34, "to": 49}}
    progress = get_progress(doc)
    assert progress["current_page"] == 34
    assert progress["answered_on_page"] == 0
    assert progress["page_coverage"] == {}


def test_get_question_budget_from_triage() -> None:
    doc = MagicMock()
    doc.meta = {
        "question_progress": {
            "page_coverage": {"34": {"question_budget": 47, "aspects": []}},
        }
    }
    assert get_question_budget(doc, 34) == 47


def test_record_answer_increments_counter() -> None:
    doc_id = uuid.uuid4()
    assertion_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    doc.status = "ready"
    doc.meta = {
        "selected_range": {"from": 1, "to": 3},
        "question_progress": {
            "current_page": 1,
            "answered_ids": [],
            "answered_on_page": 0,
            "generated_on_page": 5,
            "generation_pending": False,
            "page_coverage": {},
        },
    }
    db = MagicMock()
    db.get.return_value = doc
    db.execute.return_value.mappings.return_value.first.return_value = None

    with patch("app.services.question_pool.maybe_refill_pool", return_value=None):
        record_answer(db, doc_id, assertion_id)

    progress = doc.meta["question_progress"]
    assert progress["answered_on_page"] == 1
    assert str(assertion_id) in progress["answered_ids"]
    db.commit.assert_called_once()


def test_record_answer_skips_duplicate_increment() -> None:
    doc_id = uuid.uuid4()
    assertion_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    doc.status = "ready"
    doc.meta = {
        "selected_range": {"from": 1, "to": 3},
        "question_progress": {
            "current_page": 1,
            "answered_ids": [str(assertion_id)],
            "answered_on_page": 1,
            "generated_on_page": 5,
            "generation_pending": False,
            "page_coverage": {},
        },
    }
    db = MagicMock()
    db.get.return_value = doc

    with patch("app.services.question_pool.maybe_refill_pool", return_value=None):
        record_answer(db, doc_id, assertion_id)

    progress = doc.meta["question_progress"]
    assert progress["answered_on_page"] == 1
    assert progress["answered_ids"] == [str(assertion_id)]


def test_clear_stale_generation_pending_clears_without_active_job() -> None:
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    doc.meta = {
        "selected_range": {"from": 1, "to": 3},
        "question_progress": {
            "current_page": 1,
            "answered_ids": [],
            "answered_on_page": 0,
            "generated_on_page": 0,
            "generation_pending": True,
            "page_coverage": {},
        },
    }
    db = MagicMock()
    db.get.return_value = doc

    def execute_side_effect(statement, params=None):
        sql = str(statement)
        mock = MagicMock()
        if "status = 'running'" in sql:
            mock.scalar.return_value = None
        return mock

    db.execute.side_effect = execute_side_effect

    with patch("app.services.question_pool.save_progress") as save:
        clear_stale_generation_pending(db, doc)

    save.assert_called_once()
    assert doc.meta["question_progress"]["generation_pending"] is False


def test_ensure_question_pool_requeues_triage_when_no_coverage() -> None:
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    doc.status = "ready"
    doc.account_id = None
    doc.meta = {
        "selected_range": {"from": 34, "to": 49},
        "question_pool_initialized": True,
        "question_progress": {
            "current_page": 34,
            "answered_ids": [],
            "answered_on_page": 0,
            "generated_on_page": 0,
            "generation_pending": False,
            "page_coverage": {},
        },
    }
    db = MagicMock()
    db.get.return_value = doc

    with (
        patch("app.services.question_pool.release_stuck_generation"),
        patch("app.services.question_pool.kick_generation_sync"),
        patch("app.services.question_pool.next_assertion_id", return_value=None),
        patch("app.services.question_pool.enqueue_page_triage") as triage,
    ):
        triage.return_value = MagicMock()
        ensure_question_pool(db, doc_id)

    triage.assert_called_once()
    assert triage.call_args.kwargs["page"] == 34


def test_reset_for_new_page_range_clears_pool_state() -> None:
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    doc.account_id = None
    doc.artifact_captured_at = None
    doc.created_at = None
    doc.meta = {
        "selected_range": {"from": 1, "to": 50},
        "question_pool_initialized": True,
        "question_progress": {
            "current_page": 50,
            "answered_ids": ["a"] * 40,
            "page_coverage": {"50": {"question_budget": 20}},
        },
    }

    db = MagicMock()

    from app.services.question_pool import reset_for_new_page_range

    reset_for_new_page_range(db, doc, {"from": 51, "to": 100})

    assert doc.meta["selected_range"] == {"from": 51, "to": 100}
    assert "question_pool_initialized" not in doc.meta
    assert doc.meta["question_progress"]["current_page"] == 51
    assert doc.meta["question_progress"]["answered_ids"] == []
    assert doc.meta["question_progress"]["page_coverage"] == {}
    assert doc.index_progress == 0
    db.execute.assert_called_once()

    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    doc.status = "ready"
    doc.account_id = None
    doc.meta = {
        "selected_range": {"from": 10, "to": 12},
        "question_progress": {
            "current_page": 10,
            "answered_ids": ["a", "b", "c"],
            "generation_pending": False,
            "page_coverage": {
                "10": {
                    "question_budget": 47,
                    "aspects": [{"key": "x", "label": "X", "asked": False}],
                    "coverage_complete": False,
                }
            },
        },
    }
    db = MagicMock()
    db.get.return_value = doc

    with (
        patch("app.services.question_pool.count_assertions_on_page", return_value=5),
        patch("app.services.question_pool.count_answered_on_page", return_value=REFILL_AFTER_ANSWERED),
        patch("app.services.question_pool._count_available", return_value=2),
        patch("app.services.question_pool.enqueue_page_batch") as enqueue,
    ):
        enqueue.return_value = MagicMock()
        maybe_refill_pool(db, doc_id)

    enqueue.assert_called_once()
    kwargs = enqueue.call_args.kwargs
    assert kwargs["page"] == 10
    assert kwargs["batch_size"] == 5
    assert kwargs["start_sequence"] == 5
