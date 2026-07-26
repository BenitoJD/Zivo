"""Tutor chat must see the on-screen MCQ, not only RAG excerpts."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.graphs.chat_graph import retrieve_context
from app.services.learn_chat_context import format_active_question_retrieval_chunk


def test_format_active_question_retrieval_chunk_includes_stem() -> None:
    db = MagicMock()
    with patch(
        "app.services.learn_chat_context._assertion_mcq",
        return_value={
            "stem": "What feature of CD-35 2722 B challenges moon/planet distinction?",
            "options": ["Its large size", "Its orbit"],
            "correct_index": 0,
        },
    ):
        text = format_active_question_retrieval_chunk(db, "aid-1", page=11)
    assert text is not None
    assert "CD-35 2722 B" in text
    assert "Source page: 11" in text
    assert "do not claim it is missing" in text


def test_retrieve_context_pins_active_question_chunk() -> None:
    doc_id = uuid.uuid4()
    db = MagicMock()
    scope = {
        "current_assertion_id": "q-astro",
        "current_page": 11,
        "page_start": 11,
        "page_end": 11,
    }
    with (
        patch(
            "app.graphs.chat_graph.retrieve_document_chunks",
            return_value=[{"document_id": str(doc_id), "text": "Some page copy.", "page_start": 11}],
        ),
        patch(
            "app.graphs.chat_graph.format_active_question_retrieval_chunk",
            return_value="Active quiz question: CD-35 2722 B",
        ),
    ):
        out = retrieve_context(
            {"query": "Explain CD-35 2722 B", "scope": scope, "document_ids": [str(doc_id)]},
            db=db,
        )
    chunks = out["retrieved_chunks"]
    assert chunks[0]["chunk_id"] == "active_question"
    assert "CD-35 2722 B" in chunks[0]["text"]
    assert "CD-35 2722 B" in out["context_block"]
