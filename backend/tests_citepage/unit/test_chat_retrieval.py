"""Scoped chat retrieval helpers."""

from unittest.mock import MagicMock, patch
import uuid

from app.services.chat_retrieval import _extract_page_range, retrieve_document_chunks


def test_extract_page_range_natural_language() -> None:
    assert _extract_page_range("what is on page 5") == (5, 5)
    assert _extract_page_range("pages 3 to 7") == (3, 7)
    assert _extract_page_range("on page 12") == (12, 12)


def test_extract_page_range_ignores_citepage_labels() -> None:
    assert _extract_page_range("[p.5] mitochondria") == (None, None)
    assert _extract_page_range("see p. 9 for details") == (None, None)


def test_learn_mode_uses_current_page_not_rag_window() -> None:
    """Wide page_start/page_end from RAG window must not block the current-page fast path."""
    db = MagicMock()
    doc_id = uuid.uuid4()
    page_chunks = [
        {"chunk_id": "a", "document_id": str(doc_id), "page_start": 5, "page_end": 5, "text": "mitochondria"},
        {"chunk_id": "b", "document_id": str(doc_id), "page_start": 5, "page_end": 5, "text": "ATP"},
    ]

    with (
        patch("app.services.chat_retrieval.fetch_chunks_for_page_range", return_value=page_chunks) as fetch,
        patch("app.services.chat_retrieval.embed_query") as embed,
        patch("app.services.chat_retrieval.rerank_chunks") as rerank,
    ):
        out = retrieve_document_chunks(
            db,
            document_ids=[doc_id],
            query="what is ATP?",
            scope={"current_page": 5, "page_start": 1, "page_end": 6},
        )

    assert out == page_chunks
    fetch.assert_called_once_with(db, document_ids=[doc_id], page_start=5, page_end=5)
    embed.assert_not_called()
    rerank.assert_not_called()
