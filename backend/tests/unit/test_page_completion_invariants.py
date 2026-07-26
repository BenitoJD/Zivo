"""Invariant: a learner is never STRANDED.

The recurring failure class from the 2026-06-28 audit was "a page can never
complete → no next question → the learner can't advance". These pin the
completion state machine so that class can't silently return:

A page must always be in one of two states —
  (a) RESOLVED: is_page_complete() is True (the queue advances), or
  (b) IN PROGRESS: there is a next question to answer.
Never a third, dead state.

Resolved happens when the page is non-content, or every aspect has been asked
(including aspects ABANDONED by the stall guard after repeated failed
generation). These tests assert exactly those transitions.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.services.question_pool import (
    is_coverage_complete,
    is_non_content_page,
    is_page_complete,
)


def _doc(page: int, coverage: dict) -> MagicMock:
    doc = MagicMock()
    doc.id = uuid.uuid4()
    doc.meta = {"question_progress": {"current_page": page, "page_coverage": {str(page): coverage}}}
    return doc


def _progress(page: int) -> dict:
    return {"current_page": page, "answered_on_page": 0, "answered_ids": [], "generation_pending": False}


def test_non_content_page_is_resolved() -> None:
    # A cover/blank page yields no questions — it must still complete so the
    # learner moves on (the blank-page strand bug).
    doc = _doc(1, {"non_content": True, "aspects": []})
    assert is_non_content_page(doc, 1) is True
    assert is_coverage_complete(doc, 1) is True
    with patch("app.services.question_pool.next_assertion_id", return_value=None):
        assert is_page_complete(MagicMock(), doc, _progress(1), page_ids=[]) is True


def test_all_aspects_asked_resolves_even_with_an_abandoned_aspect() -> None:
    # An aspect generation could never satisfy is abandoned (asked=True) by the
    # stall guard, so coverage completes and the page resolves (the
    # un-generatable-aspect strand bug).
    cov = {
        "non_content": False,
        "aspects": [
            {"key": "a", "asked": True},
            {"key": "b", "asked": True, "abandoned": True},
        ],
    }
    doc = _doc(2, cov)
    assert is_coverage_complete(doc, 2) is True
    with patch("app.services.question_pool.next_assertion_id", return_value=None):
        assert is_page_complete(MagicMock(), doc, _progress(2), page_ids=["q1"]) is True


def test_in_progress_page_is_not_complete_but_has_a_next_question() -> None:
    # Healthy in-progress state: an unasked aspect remains, but there IS a ready
    # question — not complete, and crucially not stranded.
    cov = {"non_content": False, "aspects": [{"key": "a", "asked": False}]}
    doc = _doc(3, cov)
    with patch("app.services.question_pool.next_assertion_id", return_value="qid") as nxt:
        complete = is_page_complete(MagicMock(), doc, _progress(3), page_ids=["q1"])
    assert complete is False
    assert nxt.called  # a next question exists → learner can proceed


def test_served_pool_exhausted_resolves_without_active_batch() -> None:
    # Cook produced fewer MCQs than triage budget; learner answered them all and
    # no generate job is running — must advance (LLM stall / abandoned aspects).
    cov = {
        "non_content": False,
        "aspects": [{"key": "a", "asked": False}],
        "question_budget": 15,
    }
    doc = _doc(2, cov)
    progress = {
        "current_page": 2,
        "answered_ids": ["q1", "q2"],
        "generation_pending": False,
    }
    with (
        patch("app.services.question_pool.next_assertion_id", return_value=None),
        patch(
            "app.services.question_pool_jobs._has_active_generate_job_for_page",
            return_value=False,
        ),
        patch("app.services.question_pool.get_question_budget", return_value=15),
    ):
        assert is_page_complete(
            MagicMock(), doc, progress, page_ids=["q1", "q2"]
        ) is True


def test_empty_aspects_without_non_content_is_treated_as_unresolved_not_complete() -> None:
    # The dangerous shape (0 aspects + not flagged non_content) must NOT be
    # reported complete by coverage — triage is responsible for never persisting
    # it (it flags such pages non_content; see test_page_triage). This pins that
    # is_coverage_complete won't paper over a genuinely unknown page.
    doc = _doc(4, {"non_content": False, "aspects": []})
    assert is_coverage_complete(doc, 4) is False
