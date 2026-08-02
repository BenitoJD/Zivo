"""Background-prep generation cap — one huge doc must not flood the queue."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.background_prep import count_assertions_for_document


def test_count_assertions_for_document() -> None:
    db = MagicMock()
    db.execute.return_value.scalar.return_value = 42
    n = count_assertions_for_document(db, "00000000-0000-4000-8000-000000000001")
    assert n == 42
    db.execute.assert_called_once()


def test_count_assertions_for_document_none() -> None:
    db = MagicMock()
    db.execute.return_value.scalar.return_value = None
    assert count_assertions_for_document(db, "00000000-0000-4000-8000-000000000001") == 0


def test_background_prep_cap_halts_chaining() -> None:
    """tick_background_cook stops enqueuing once the doc hits the cap."""
    from app.services.background_prep import tick_background_cook
    from app.services.question_pool_jobs import BACKGROUND_PREP_MAX_QUESTIONS

    db = MagicMock()
    doc = MagicMock()
    doc.meta = {"prep_mode": "background"}
    db.get.return_value = doc

    with (
        patch("app.services.background_prep.is_background_prep", return_value=True),
        patch("app.services.background_prep.prep_phase", return_value="cooking"),
        patch("app.services.background_prep.sync_rag_window", return_value=[]),
        patch("app.services.background_prep._next_page_needing_triage", return_value=None),
        patch("app.services.background_prep._next_page_needing_cook", return_value=3),
        patch(
            "app.services.background_prep.count_assertions_for_document",
            return_value=BACKGROUND_PREP_MAX_QUESTIONS,
        ),
        patch("app.services.background_prep.maybe_complete_prep") as complete,
        patch("app.services.question_pool_jobs.enqueue_page_batch") as enqueue,
    ):
        out = tick_background_cook(db, "00000000-0000-4000-8000-000000000001")

    assert out is None
    complete.assert_called_once()
    enqueue.assert_not_called()


def test_background_prep_below_cap_keeps_cooking() -> None:
    from app.services.background_prep import tick_background_cook

    db = MagicMock()
    doc = MagicMock()
    doc.meta = {"prep_mode": "background"}
    db.get.return_value = doc

    with (
        patch("app.services.background_prep.is_background_prep", return_value=True),
        patch("app.services.background_prep.prep_phase", return_value="cooking"),
        patch("app.services.background_prep.sync_rag_window", return_value=[]),
        patch("app.services.background_prep._next_page_needing_triage", return_value=None),
        patch("app.services.background_prep._next_page_needing_cook", return_value=3),
        patch("app.services.background_prep.count_assertions_for_document", return_value=10),
        patch("app.services.background_prep.count_assertions_on_page", return_value=2),
        patch("app.services.background_prep.get_question_budget", return_value=5),
        patch(
            "app.services.question_pool_jobs.enqueue_page_batch",
            return_value=MagicMock(),
        ) as enqueue,
    ):
        tick_background_cook(db, "00000000-0000-4000-8000-000000000001")

    enqueue.assert_called_once()
