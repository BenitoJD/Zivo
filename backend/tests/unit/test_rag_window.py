"""Unit tests for sliding-window RAG page selection."""

from __future__ import annotations

from app.services.rag_window import MAX_RAG_PAGES, chat_rag_window


def _study(n: int) -> list[int]:
    return list(range(1, n + 1))


def test_window_page_one() -> None:
    assert chat_rag_window(1, _study(100)) == [1, 2]


def test_window_page_two() -> None:
    assert chat_rag_window(2, _study(100)) == [1, 2, 3]


def test_window_page_three() -> None:
    assert chat_rag_window(3, _study(100)) == [1, 2, 3, 4, 5]


def test_window_page_fifty_slides() -> None:
    window = chat_rag_window(50, _study(100))
    assert len(window) == MAX_RAG_PAGES
    assert window == [47, 48, 49, 50, 51, 52]


def test_window_sparse_study_pages() -> None:
    study = [1, 5, 10, 15, 20]
    assert chat_rag_window(10, study) == [1, 5, 10]


# --- readiness: the learner walking past the saved window -------------------
#
# Regression for a production deadlock. The saved window is only rewritten when
# an ingest runs, so a learner who advances lands on a page outside it. Readiness
# was judged against that stale window (and a sticky `rag_window_ready` flag), so
# it answered "ready", ensure_question_pool never enqueued the ingest, page triage
# deferred forever on the unindexed page, and generation_pending never cleared -
# the UI sat at "Planning the quiz" re-enqueueing a no-op job every 60s.

import uuid

from app.models import Document
from app.services import rag_window as rw


def _doc(*, current_page: int, window_pages: list[int], ready_flag: bool) -> Document:
    return Document(
        id=uuid.uuid4(),
        meta={
            "selected_range": {"from": 2, "to": 34, "pages": list(range(2, 35))},
            "rag_window": {"pages": window_pages, "page_start": window_pages[0], "page_end": window_pages[-1]},
            "rag_window_ready": ready_flag,
            "question_progress": {"current_page": current_page},
        },
    )


def test_not_ready_when_current_page_escaped_the_saved_window(monkeypatch) -> None:
    """The exact prod state: window [4..9] + ready flag, learner on page 10."""
    monkeypatch.setattr(rw, "indexed_pages_for_document", lambda db, doc_id: set(range(2, 10)))
    doc = _doc(current_page=10, window_pages=[4, 5, 6, 7, 8, 9], ready_flag=True)
    assert rw.is_rag_window_ready(None, doc.id, doc) is False


def test_ready_when_current_page_inside_window_and_indexed(monkeypatch) -> None:
    """The sticky flag still short-circuits while the learner is inside the window."""
    monkeypatch.setattr(rw, "indexed_pages_for_document", lambda db, doc_id: set(range(2, 10)))
    doc = _doc(current_page=8, window_pages=[4, 5, 6, 7, 8, 9], ready_flag=True)
    assert rw.is_rag_window_ready(None, doc.id, doc) is True


def test_not_ready_when_escaped_window_and_new_pages_unindexed(monkeypatch) -> None:
    """Re-derived window for page 10 needs 10/11/12; none are indexed."""
    monkeypatch.setattr(rw, "indexed_pages_for_document", lambda db, doc_id: set(range(2, 10)))
    doc = _doc(current_page=10, window_pages=[4, 5, 6, 7, 8, 9], ready_flag=False)
    assert rw.is_rag_window_ready(None, doc.id, doc) is False


def test_ready_once_the_new_window_is_indexed(monkeypatch) -> None:
    """After the ingest catches up, the same state reports ready and the loop proceeds."""
    monkeypatch.setattr(rw, "indexed_pages_for_document", lambda db, doc_id: set(range(2, 13)))
    doc = _doc(current_page=10, window_pages=[4, 5, 6, 7, 8, 9], ready_flag=True)
    assert rw.is_rag_window_ready(None, doc.id, doc) is True
