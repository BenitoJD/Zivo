"""Unit tests for rolling question pool."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.services.question_pool import (
    REFILL_AFTER_ANSWERED,
    _merge_progress,
    clear_stale_generation_pending,
    effective_question_budget,
    ensure_question_pool,
    get_progress,
    get_question_budget,
    maybe_refill_pool,
    maybe_transition_prefetch,
    record_answer,
    release_stuck_generation,
    should_transition_prefetch,
)


def test_bump_aspect_attempts_abandons_after_max() -> None:
    from app.services.question_pool import MAX_ASPECT_ATTEMPTS
    from app.services.question_pool_jobs import bump_aspect_attempts

    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    doc.meta = {
        "question_progress": {
            "current_page": 2,
            "page_coverage": {
                "2": {
                    "aspects": [
                        {"key": "x", "asked": False, "gen_attempts": MAX_ASPECT_ATTEMPTS - 1},
                        {"key": "y", "asked": False, "gen_attempts": 0},
                    ]
                }
            },
        }
    }
    db = MagicMock()
    db.get.return_value = doc

    with patch("app.services.question_pool_jobs.save_progress") as sp:
        bump_aspect_attempts(db, doc_id, 2, {"x", "y"})

    entry = sp.call_args[0][2]["page_coverage"]["2"]
    by_key = {a["key"]: a for a in entry["aspects"]}
    # 'x' hit the attempt cap -> abandoned (asked) so it can't block completion.
    assert by_key["x"]["asked"] is True
    assert by_key["x"]["abandoned"] is True
    # 'y' just incremented, still in play.
    assert by_key["y"]["asked"] is False
    assert by_key["y"]["gen_attempts"] == 1
    db.commit.assert_called_once()


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
    # Honoured as-is (no floor, no artificial ceiling).
    assert get_question_budget(doc, 34) == 47


def test_get_question_budget_no_floor_or_ceiling() -> None:
    def _doc(budget: int) -> MagicMock:
        d = MagicMock()
        d.meta = {"question_progress": {"page_coverage": {"7": {"question_budget": budget, "aspects": []}}}}
        return d

    # A triaged budget below the old floor of 5 is honoured exactly...
    assert get_question_budget(_doc(2), 7) == 2
    assert get_question_budget(_doc(0), 7) == 0
    # ...and dense pages keep the full plan (no 150 clamp).
    assert get_question_budget(_doc(300), 7) == 300


def test_get_question_budget_ignores_legacy_preferred_meta() -> None:
    """Old docs may still carry preferred_question_budget; triage owns the plan."""
    doc = MagicMock()
    doc.meta = {
        "preferred_question_budget": 5,
        "selected_range": {"from": 1, "to": 3, "pages": [1, 2, 3]},
        "question_progress": {"page_coverage": {"1": {"question_budget": 40, "aspects": []}}},
    }
    assert get_question_budget(doc, 1) == 40


def test_effective_budget_equals_full_plan() -> None:
    doc = MagicMock()
    doc.meta = {"question_progress": {"page_coverage": {"3": {"question_budget": 200, "aspects": []}}}}
    # No generate-ahead pacing — always the full page plan.
    assert effective_question_budget(doc, 3, {"answered_on_page": 0}) == 200
    assert effective_question_budget(doc, 3, {"answered_on_page": 20}) == 200
    assert effective_question_budget(doc, 3, {"answered_on_page": 130}) == 200
    assert effective_question_budget(doc, 3, None) == 200


def test_pool_ahead_defaults_are_deep_enough_for_seamless() -> None:
    """Jobs law knobs: deep low-water + early transition (batch mechanics, not page caps)."""
    from app.services.question_pool import (
        EAGER_TRIAGE_LOOKAHEAD,
        READY_LOW_WATER,
        TRANSITION_GENERATION_RATIO,
        TRANSITION_PREFETCH_RATIO,
    )

    assert READY_LOW_WATER >= 20
    assert TRANSITION_PREFETCH_RATIO <= 0.45
    assert TRANSITION_GENERATION_RATIO <= 0.15
    assert EAGER_TRIAGE_LOOKAHEAD >= 5


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
        patch("app.services.question_pool_jobs._has_active_generate_job_for_page", return_value=False),
        patch("app.services.question_pool_jobs.enqueue_page_batch") as enqueue,
    ):
        enqueue.return_value = MagicMock()
        maybe_refill_pool(db, doc_id)

    enqueue.assert_called_once()
    kwargs = enqueue.call_args.kwargs
    assert kwargs["page"] == 10
    assert kwargs["batch_size"] == 5
    assert kwargs["start_sequence"] == 5


def test_maybe_refill_pool_while_generation_pending_if_no_page_job() -> None:
    """Doc-level generation_pending must NOT freeze refill when this page has no active batch.

    Classic freeze: next-page prefetch sets generation_pending, current page drains,
    maybe_refill bailed on the flag → empty spinner. Page-scoped job check fixes it.
    """
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
            "generation_pending": True,
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
        patch("app.services.question_pool_jobs.count_answered_on_page", return_value=3),
        patch("app.services.question_pool_jobs._count_available", return_value=2),
        patch("app.services.question_pool_jobs._has_active_generate_job_for_page", return_value=False),
        patch("app.services.question_pool_jobs.enqueue_page_batch") as enqueue,
    ):
        enqueue.return_value = MagicMock()
        maybe_refill_pool(db, doc_id)

    enqueue.assert_called_once()


def test_maybe_refill_pool_skips_when_page_batch_active() -> None:
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    doc.status = "ready"
    doc.account_id = None
    doc.meta = {
        "selected_range": {"from": 10, "to": 12},
        "question_progress": {
            "current_page": 10,
            "answered_ids": [],
            "generation_pending": True,
            "page_coverage": {
                "10": {
                    "question_budget": 47,
                    "aspects": [{"key": "x", "asked": False}],
                    "coverage_complete": False,
                }
            },
        },
    }
    db = MagicMock()
    db.get.return_value = doc

    with (
        patch("app.services.question_pool_jobs._has_active_generate_job_for_page", return_value=True),
        patch("app.services.question_pool_jobs.enqueue_page_batch") as enqueue,
    ):
        maybe_refill_pool(db, doc_id)

    enqueue.assert_not_called()


def test_on_batch_completed_chains_refill() -> None:
    from app.services.question_pool_jobs import on_batch_completed

    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    doc.account_id = None
    doc.meta = {
        "question_progress": {
            "current_page": 3,
            "generation_pending": True,
            "page_coverage": {"3": {"question_budget": 40, "aspects": [{"key": "a"}]}},
        }
    }
    db = MagicMock()
    db.get.return_value = doc

    with (
        patch("app.services.question_pool_jobs.count_assertions_on_page", return_value=5),
        patch("app.services.question_pool_jobs.save_progress"),
        patch("app.services.question_pool_jobs._maybe_enqueue_initial_pool_remainder", return_value=None),
        patch("app.services.question_pool_jobs.maybe_refill_pool") as refill,
    ):
        on_batch_completed(db, doc_id, page=3, saved=2)

    refill.assert_called_once_with(db, doc_id)


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


def test_should_transition_prefetch_after_ratio() -> None:
    # Default TRANSITION_PREFETCH_RATIO is 0.45 — fire well before page end.
    assert should_transition_prefetch(6, 15) is False  # 0.40
    assert should_transition_prefetch(7, 15) is True  # ~0.47


def test_maybe_transition_prefetch_enqueues_once() -> None:
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    doc.status = "ready"
    doc.account_id = None
    doc.meta = {
        "question_progress": {
            "current_page": 1,
            "answered_on_page": 7,
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


def test_select_next_assertion_prefers_focus_concept() -> None:
    from app.services.question_pool import select_next_assertion

    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    progress = {
        "current_page": 1,
        "answered_ids": [],
        "focus_concept": "Photosynthesis",
        "selection_policy": "sequence",
    }
    candidates = [str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())]
    preferred = [candidates[1]]
    db = MagicMock()

    with (
        patch("app.services.question_pool.page_assertion_ids", return_value=candidates),
        patch(
            "app.services.question_pool._candidates_matching_concept_label",
            return_value=preferred,
        ) as match,
    ):
        chosen = select_next_assertion(db, doc_id, doc, progress)

    assert chosen == preferred[0]
    match.assert_called_once()
    assert match.call_args[0][2] == "Photosynthesis"


def test_select_next_assertion_miss_without_lineage_falls_back_to_reinforce() -> None:
    from app.services.question_pool import select_next_assertion

    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    last_id = str(uuid.uuid4())
    candidates = [str(uuid.uuid4()), str(uuid.uuid4())]
    progress = {
        "current_page": 1,
        "answered_ids": [],
        "selection_policy": "difficulty_edge",
        "last_assertion_id": last_id,
        "last_correct": False,
        "last_concept_key": "cell-membrane",
    }
    db = MagicMock()

    with (
        patch("app.services.question_pool.page_assertion_ids", return_value=candidates),
        patch("app.services.question_pool._difficulty_for_ids", return_value={}),
        patch(
            "app.services.question_pool._concept_keys_for_ids",
            return_value={c: "cell-membrane" for c in candidates},
        ),
        patch("app.services.question_pool._lineage_successors", return_value={}),
        patch("app.services.selection.choose_next_assertion", return_value=candidates[0]) as choose,
        patch("app.services.selection.build_learner_state") as state_fn,
    ):
        state_fn.return_value = MagicMock(
            last_assertion_id=last_id,
            last_correct=False,
            last_concept_key="cell-membrane",
        )
        chosen = select_next_assertion(db, doc_id, doc, progress)

    assert chosen == candidates[0]
    # Cold lineage on miss → concept_reinforce policy handed to chooser.
    assert choose.call_args[0][0] == "concept_reinforce"


def test_write_batch_lineage_links_same_concept_and_sequence() -> None:
    from app.graphs.generation_graph import _write_batch_lineage

    a1, a2, a3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    finalized = [
        {"_assertion_id": str(a1), "primary_concept_key": "osmosis"},
        {"_assertion_id": str(a2), "primary_concept_key": "osmosis"},
        {"_assertion_id": str(a3), "primary_concept_key": "diffusion"},
    ]
    db = MagicMock()
    follow = uuid.uuid4()
    harder = uuid.uuid4()

    with patch("app.repositories.intel._concept_id", side_effect=[follow, harder]):
        _write_batch_lineage(db, finalized)

    db.execute.assert_called_once()
    rows = db.execute.call_args[0][1]
    kinds = {(r["f"], r["t"], r["lt"]) for r in rows}
    assert (a1, a2, follow) in kinds  # same-concept follow-up
    assert (a1, a2, harder) in kinds  # sequential harder_than
    assert (a2, a3, harder) in kinds
