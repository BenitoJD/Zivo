"""Newspaper chat scope pins retrieval to the question page."""

from unittest.mock import MagicMock, patch

from app.services.chat_scope import normalize_chat_scope


def test_normalize_chat_scope_newspaper_single_page() -> None:
    doc = MagicMock()
    doc.meta = {"newspaper": True}
    with patch("app.services.chat_scope.is_newspaper_document", return_value=True):
        out = normalize_chat_scope({"current_page": 15}, doc)
    assert out["current_page"] == 15
    assert out["page_start"] == 15
    assert out["page_end"] == 15
    assert out["rag_window_pages"] == [15]
