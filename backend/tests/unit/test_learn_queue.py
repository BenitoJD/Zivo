"""Learn-queue state — numbering, resume, page complete."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.services.question_pool import (
    advance_to_next_page,
    build_learn_queue_state,
    is_page_complete,
)


def _doc_with_coverage(page: int = 34, budget: int = 47) -> MagicMock:
    doc = MagicMock()
    doc.id = uuid.uuid4()
    doc.meta = {
        "selected_range": {"from": 34, "to": 36},
        "question_progress": {
            "current_page": page,
            "answered_ids": ["id-1", "id-2"],
            "generation_pending": False,
            "page_coverage": {
                str(page): {
                    "question_budget": budget,
                    "aspects": [{"key": "a", "label": "A", "asked": True, "answered": True}],
                    "coverage_complete": False,
                }
            },
        },
    }
    return doc


def test_build_learn_queue_state_resume_at_question_three() -> None:
    doc = _doc_with_coverage()
    doc_id = doc.id
    progress = doc.meta["question_progress"]
    db = MagicMock()

    page_ids = [f"id-{i}" for i in range(1, 11)]

    with (
        patch("app.services.question_pool.page_assertion_ids", return_value=page_ids),
        patch("app.services.question_pool._count_available", return_value=8),
        patch("app.services.question_pool.is_page_complete", return_value=False),
        patch("app.services.question_pool.get_page_coverage", return_value={"question_budget": 47}),
        patch("app.services.rag_window.get_rag_window", return_value=[]),
        patch("app.services.rag_window.is_rag_window_ready", return_value=True),
    ):
        state = build_learn_queue_state(db, doc_id, doc, progress)

    assert state["question_number"] == 3
    assert state["questions_answered"] == 2
    # Learner-facing page Y = plan_budget; session_soft is pacing-only.
    assert state["question_budget"] == 47
    assert state["plan_budget"] == 47
    assert state["generation_cap"] == 47
    assert state["session_soft"] == 20
    assert state["document_budget"] == 47
    assert state["current_assertion_id"] == "id-3"
    assert state["page_complete"] is False
    assert state["page_triage_complete"] is True
    assert state["generated_on_page"] == 10


def test_is_page_complete_when_budget_reached_and_pool_empty() -> None:
    doc = _doc_with_coverage()
    progress = doc.meta["question_progress"]
    db = MagicMock()

    with (
        patch("app.services.question_pool.next_assertion_id", return_value=None),
        patch("app.services.question_pool.count_assertions_on_page", return_value=47),
        patch("app.services.question_pool.is_coverage_complete", return_value=False),
    ):
        assert is_page_complete(db, doc, progress) is True


def test_is_page_complete_ignores_stale_pending_when_budget_met() -> None:
    """Next-page prefetch can leave generation_pending True — must not strand page turn."""
    doc = _doc_with_coverage(budget=10)
    progress = doc.meta["question_progress"]
    progress["generation_pending"] = True
    db = MagicMock()

    with (
        patch("app.services.question_pool.next_assertion_id", return_value=None),
        patch("app.services.question_pool.count_assertions_on_page", return_value=10),
        patch("app.services.question_pool.is_coverage_complete", return_value=False),
    ):
        assert is_page_complete(db, doc, progress) is True


def test_is_page_complete_false_while_pending_and_room_left() -> None:
    doc = _doc_with_coverage(budget=47)
    progress = doc.meta["question_progress"]
    progress["generation_pending"] = True
    db = MagicMock()

    with (
        patch("app.services.question_pool.next_assertion_id", return_value=None),
        patch("app.services.question_pool.count_assertions_on_page", return_value=5),
        patch("app.services.question_pool.is_coverage_complete", return_value=False),
    ):
        assert is_page_complete(db, doc, progress) is False


def test_advance_to_next_page_enqueues_triage_for_new_page() -> None:
    doc = _doc_with_coverage(page=34)
    db = MagicMock()
    db.get.return_value = doc
    saved: dict = {}

    def _capture_save(_db, _doc, patch, **kwargs):
        saved.update(patch)

    with (
        patch("app.services.question_pool_jobs.enqueue_page_triage") as triage,
        patch("app.services.question_pool_jobs.count_assertions_on_page", return_value=0),
        patch("app.services.question_pool_jobs.save_progress", side_effect=_capture_save),
        patch("app.services.rag_window.is_rag_window_ready", return_value=True),
    ):
        triage.return_value = MagicMock()
        advance_to_next_page(db, doc)

    assert saved["current_page"] == 35
    triage.assert_called_once()
    assert triage.call_args.kwargs["page"] == 35
