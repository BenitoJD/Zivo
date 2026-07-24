"""Integration-style unit tests: budget planner wired into triage / pool / learn-queue."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.services.question_budget import BUDGET_VERSION, SESSION_SOFT
from app.services.question_pool import (
    REFILL_BATCH_SIZE,
    build_learn_queue_state,
    get_question_budget,
    page_budgets_for_document,
)
from app.services.question_pool_jobs import maybe_refill_pool


def _doc(
    *,
    page: int = 3,
    budget: int = 7,
    confidence: str = "high",
    pages: list[int] | None = None,
) -> MagicMock:
    pages = pages or [page]
    coverage = {
        str(p): {
            "question_budget": budget if p == page else 2,
            "aspects": [{"key": "a", "label": "A", "asked": False, "answered": False}],
            "coverage_complete": False,
            "budget_confidence": confidence,
            "budget_version": BUDGET_VERSION,
            "budget_mode": "learn",
            "n_cov": float(budget),
        }
        for p in pages
    }
    doc = MagicMock()
    doc.id = uuid.uuid4()
    doc.status = "ready"
    doc.meta = {
        "selected_range": {"from": pages[0], "to": pages[-1]},
        "question_progress": {
            "current_page": page,
            "answered_ids": [],
            "generation_pending": False,
            "page_coverage": coverage,
        },
    }
    return doc


def test_learn_queue_exposes_plan_vs_session_soft() -> None:
    doc = _doc(budget=7, pages=[3, 4])
    progress = doc.meta["question_progress"]
    db = MagicMock()

    with (
        patch("app.services.question_pool.page_assertion_ids", return_value=["q1"]),
        patch("app.services.question_pool._count_available", return_value=1),
        patch("app.services.question_pool.is_page_complete", return_value=False),
        patch("app.services.question_pool.select_next_assertion", return_value="q1"),
        patch("app.services.rag_window.get_rag_window", return_value=[]),
        patch("app.services.rag_window.is_rag_window_ready", return_value=True),
    ):
        state = build_learn_queue_state(db, doc.id, doc, progress)

    assert state["plan_budget"] == 7
    assert state["question_budget"] == 7
    assert state["session_soft"] == SESSION_SOFT
    assert state["session_soft"] != state["plan_budget"]
    assert state["document_budget"] == 9  # 7 + 2
    assert state["budget_confidence"] == "high"
    assert state["budget_version"] == BUDGET_VERSION


def test_page_budgets_include_newspaper_zeros() -> None:
    doc = _doc(page=1, budget=5, pages=[1, 2, 3])
    cov = doc.meta["question_progress"]["page_coverage"]
    cov["2"] = {
        "question_budget": 0,
        "aspects": [],
        "non_content": True,
        "coverage_complete": True,
    }
    assert page_budgets_for_document(doc) == [5, 0, 2]


def test_maybe_refill_stops_at_n_page_not_forever() -> None:
    """Cook stops when generated_on_page >= N_page; REFILL_BATCH_SIZE is chunk only."""
    doc = _doc(budget=7)
    db = MagicMock()
    db.get.return_value = doc

    with (
        patch(
            "app.services.question_pool_jobs._has_active_generate_job_for_page",
            return_value=False,
        ),
        patch(
            "app.services.question_pool_jobs.get_page_coverage",
            return_value=doc.meta["question_progress"]["page_coverage"]["3"],
        ),
        patch(
            "app.services.question_pool_jobs.effective_question_budget",
            return_value=7,
        ),
        patch(
            "app.services.question_pool_jobs.count_assertions_on_page",
            return_value=7,
        ),
        patch("app.services.question_pool_jobs.count_answered_on_page", return_value=3),
        patch("app.services.question_pool_jobs._count_available", return_value=0),
        patch(
            "app.services.question_pool_jobs.is_coverage_complete",
            return_value=False,
        ),
        patch("app.services.question_pool_jobs.enqueue_page_batch") as enqueue,
    ):
        assert maybe_refill_pool(db, doc.id) is None
        enqueue.assert_not_called()


def test_maybe_refill_chunks_with_refill_batch_under_remaining() -> None:
    doc = _doc(budget=20)
    db = MagicMock()
    db.get.return_value = doc
    job = MagicMock()

    with (
        patch(
            "app.services.question_pool_jobs._has_active_generate_job_for_page",
            return_value=False,
        ),
        patch(
            "app.services.question_pool_jobs.get_page_coverage",
            return_value=doc.meta["question_progress"]["page_coverage"]["3"],
        ),
        patch(
            "app.services.question_pool_jobs.effective_question_budget",
            return_value=20,
        ),
        patch(
            "app.services.question_pool_jobs.count_assertions_on_page",
            return_value=4,
        ),
        patch("app.services.question_pool_jobs.count_answered_on_page", return_value=2),
        patch("app.services.question_pool_jobs._count_available", return_value=0),
        patch(
            "app.services.question_pool_jobs.is_coverage_complete",
            return_value=False,
        ),
        patch("app.services.question_pool_jobs.enqueue_page_batch", return_value=job) as enqueue,
    ):
        assert maybe_refill_pool(db, doc.id) is job
        assert enqueue.call_args.kwargs["batch_size"] == REFILL_BATCH_SIZE
        assert enqueue.call_args.kwargs["batch_size"] < 20


def test_get_question_budget_honours_triaged_plan() -> None:
    doc = _doc(budget=11)
    assert get_question_budget(doc, 3) == 11


def test_persist_coverage_stores_planner_metadata() -> None:
    from app.graphs.page_triage_graph import _persist_triage_coverage

    db = MagicMock()
    doc_id = uuid.uuid4()
    result = {
        "question_budget": 3,
        "aspects": [{"key": "a"}],
        "rationale": "ok",
        "aspect_dedup": None,
        "content_type": "prose",
        "non_content": False,
        "programmable": False,
        "budget_confidence": "medium",
        "budget_mode": "learn",
        "budget_version": BUDGET_VERSION,
        "n_cov": 3.0,
    }
    with patch("app.graphs.page_triage_graph.save_page_coverage") as save:
        _persist_triage_coverage(
            db, doc_id, page_number=9, result=result, activity_id=None
        )
    kwargs = save.call_args.kwargs
    assert kwargs["question_budget"] == 3
    assert kwargs["budget_confidence"] == "medium"
    assert kwargs["budget_version"] == BUDGET_VERSION
    assert kwargs["n_cov"] == 3.0
