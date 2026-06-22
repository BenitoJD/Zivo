"""Scoped chat retrieval helpers."""

from app.services.chat_retrieval import _extract_page_range


def test_extract_page_range_natural_language() -> None:
    assert _extract_page_range("what is on page 5") == (5, 5)
    assert _extract_page_range("pages 3 to 7") == (3, 7)
    assert _extract_page_range("on page 12") == (12, 12)


def test_extract_page_range_ignores_citepage_labels() -> None:
    assert _extract_page_range("[p.5] mitochondria") == (None, None)
    assert _extract_page_range("see p. 9 for details") == (None, None)
