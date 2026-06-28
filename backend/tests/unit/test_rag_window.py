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
