"""Unit tests for rolling question pool."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.services.question_pool import (
    REFILL_AFTER_ANSWERED,
    _merge_progress,
    clear_stale_generation_pending,
    ensure_question_pool,
    get_progress,
    get_question_budget,
    maybe_refill_pool,
    maybe_transition_prefetch,
    record_answer,
    release_stuck_generation,
    should_transition_prefetch,
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
    assert get_question_budget(doc, 34) == 40


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

    with (
        patch("app.services.question_pool_jobs.maybe_refill_pool", return_value=None),
        patch("app.services.question_pool.save_progress_row") as save_row,
    ):
        record_answer(db, doc_id, assertion_id)

    progress = save_row.call_args[0][2]
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

    with patch("app.services.question_pool_jobs.maybe_refill_pool", return_value=None):
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

    with (
        patch("app.services.question_pool_jobs._reclaim_stale_generate_jobs", return_value=0),
        patch("app.services.question_pool_jobs._has_active_generate_job", return_value=False),
        patch("app.services.question_pool_jobs.save_progress") as save,
    ):
        clear_stale_generation_pending(db, doc)

    save.assert_called_once()
    assert save.call_args.args[2] == {"generation_pending": False}


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
        patch("app.services.question_pool_jobs.release_stuck_generation"),
        patch("app.services.question_pool_jobs.kick_generation_sync"),
        patch("app.services.question_pool_jobs.next_assertion_id", return_value=None),
        patch("app.services.question_pool_jobs._has_active_generate_job", return_value=False),
        patch("app.services.question_pool_jobs.maybe_refill_pool", return_value=None),
        patch("app.services.question_pool_jobs.enqueue_page_triage") as triage,
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

    with patch("app.services.question_pool_jobs.save_progress_row") as save_row:
        reset_for_new_page_range(db, doc, {"from": 51, "to": 100})

    assert doc.meta["selected_range"] == {"from": 51, "to": 100}
    assert "question_pool_initialized" not in doc.meta
    progress = save_row.call_args[0][2]
    assert progress["current_page"] == 51
    assert progress["answered_ids"] == []
    assert progress["page_coverage"] == {}
    assert doc.index_progress == 0
    assert db.execute.call_count == 2

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
        patch("app.services.question_pool_jobs.count_assertions_on_page", return_value=5),
        patch("app.services.question_pool_jobs.count_answered_on_page", return_value=REFILL_AFTER_ANSWERED),
        patch("app.services.question_pool_jobs._count_available", return_value=2),
        patch("app.services.question_pool_jobs.enqueue_page_batch") as enqueue,
    ):
        enqueue.return_value = MagicMock()
        maybe_refill_pool(db, doc_id)

    enqueue.assert_called_once()
    kwargs = enqueue.call_args.kwargs
    assert kwargs["page"] == 10
    assert kwargs["batch_size"] == 5
    assert kwargs["start_sequence"] == 5


def test_merge_progress_does_not_wipe_page_coverage() -> None:
    existing = {
        "current_page": 17,
        "generation_pending": False,
        "page_coverage": {"17": {"question_budget": 12, "aspects": [{"key": "a"}]}},
    }
    patch = {
        "generation_pending": True,
        "page_coverage": {},
    }
    merged = _merge_progress(existing, patch)
    assert merged["generation_pending"] is True
    assert merged["page_coverage"]["17"]["question_budget"] == 12


def test_release_stuck_generation_keeps_queued_jobs_when_not_pending() -> None:
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    doc.meta = {
        "question_progress": {
            "current_page": 17,
            "generation_pending": False,
            "page_coverage": {},
        }
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

    with (
        patch("app.services.question_pool_jobs._reclaim_stale_generate_jobs", return_value=0),
        patch("app.services.question_pool_jobs._cancel_queued_generate_jobs") as cancel,
    ):
        release_stuck_generation(db, doc_id)

    cancel.assert_not_called()
    db.commit.assert_not_called()


def test_release_stuck_generation_clears_pending_when_no_active_job() -> None:
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    doc.meta = {
        "question_progress": {
            "current_page": 17,
            "generation_pending": True,
            "page_coverage": {},
        }
    }
    db = MagicMock()
    db.get.return_value = doc

    with (
        patch("app.services.question_pool_jobs._reclaim_stale_generate_jobs", return_value=0),
        patch("app.services.question_pool_jobs._has_active_generate_job", return_value=False),
        patch("app.services.question_pool_jobs._cancel_queued_generate_jobs") as cancel,
        patch("app.services.question_pool_jobs.save_progress") as save,
    ):
        release_stuck_generation(db, doc_id)

    cancel.assert_not_called()
    save.assert_called_once_with(db, doc, {"generation_pending": False})
    db.commit.assert_called_once()


def test_release_stuck_generation_keeps_pending_when_job_queued() -> None:
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    doc.meta = {"question_progress": {"generation_pending": True, "page_coverage": {}}}
    db = MagicMock()
    db.get.return_value = doc

    with (
        patch("app.services.question_pool_jobs._reclaim_stale_generate_jobs", return_value=0),
        patch("app.services.question_pool_jobs._has_active_generate_job", return_value=True),
        patch("app.services.question_pool_jobs.save_progress") as save,
    ):
        release_stuck_generation(db, doc_id)

    save.assert_not_called()
    db.commit.assert_not_called()


def test_ensure_question_pool_skips_enqueue_when_job_active() -> None:
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    doc.status = "ready"
    doc.account_id = None
    doc.meta = {
        "selected_range": {"from": 17, "to": 35},
        "question_pool_initialized": True,
        "question_progress": {
            "current_page": 17,
            "generation_pending": True,
            "page_coverage": {},
        },
    }
    db = MagicMock()
    db.get.return_value = doc

    with (
        patch("app.services.question_pool_jobs.release_stuck_generation"),
        patch("app.services.question_pool_jobs.kick_generation_sync"),
        patch("app.services.question_pool_jobs.next_assertion_id", return_value=None),
        patch("app.services.question_pool_jobs._has_active_generate_job", return_value=True),
        patch("app.services.question_pool_jobs.maybe_refill_pool") as refill,
        patch("app.services.question_pool_jobs.enqueue_page_triage") as triage,
    ):
        ensure_question_pool(db, doc_id)

    refill.assert_not_called()
    triage.assert_not_called()


def test_ensure_question_pool_refills_when_pool_exhausted() -> None:
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    doc.status = "ready"
    doc.account_id = None
    doc.meta = {
        "selected_range": {"from": 25, "to": 33},
        "question_pool_initialized": True,
        "question_progress": {"current_page": 25, "page_coverage": {"25": {"question_budget": 15}}},
    }
    db = MagicMock()
    db.get.return_value = doc
    refill_job = MagicMock()

    with (
        patch("app.services.question_pool_jobs.release_stuck_generation"),
        patch("app.services.question_pool_jobs.kick_generation_sync") as kick,
        patch("app.services.question_pool_jobs.next_assertion_id", return_value=None),
        patch("app.services.question_pool_jobs._has_active_generate_job", return_value=False),
        patch("app.services.question_pool_jobs.maybe_refill_pool", return_value=refill_job) as refill,
        patch("app.services.question_pool_jobs._enqueue_pool_work") as enqueue_work,
    ):
        job = ensure_question_pool(db, doc_id)

    assert job is refill_job
    refill.assert_called_once_with(db, doc_id)
    enqueue_work.assert_not_called()
    # Request path is read-only now — generation runs in the background workers,
    # never inline in the request.
    kick.assert_not_called()


def test_should_transition_prefetch_after_seventy_percent() -> None:
    assert should_transition_prefetch(10, 15) is False
    assert should_transition_prefetch(11, 15) is True


def test_maybe_transition_prefetch_enqueues_once() -> None:
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    doc.status = "ready"
    doc.account_id = None
    doc.meta = {
        "question_progress": {
            "current_page": 1,
            "answered_on_page": 11,
            "transition_prep_done": {},
            "page_coverage": {"1": {"question_budget": 15, "aspects": []}},
        }
    }
    db = MagicMock()
    db.get.return_value = doc

    with patch("app.services.question_pool_jobs.enqueue_transition_prep") as enqueue:
        enqueue.return_value = MagicMock()
        maybe_transition_prefetch(db, doc_id)

    enqueue.assert_called_once()
