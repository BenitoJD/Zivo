"""Newspaper-aware tutor learn context."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.services.learn_chat_context import build_learn_chat_context


def test_newspaper_learn_chat_context_pins_source_page() -> None:
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.meta = {
        "question_pool_initialized": True,
        "newspaper": True,
        "paper_title": "The Hindu",
        "edition_date": "2026-07-26",
        "selected_range": {"from": 1, "to": 20},
    }
    db = MagicMock()

    state = {
        "current_page": 3,
        "question_number": 6,
        "question_budget": 60,
        "questions_answered": 5,
        "questions_generated": 60,
        "current_assertion_id": "q-on-page-15",
        "page_complete": False,
        "document_complete": False,
        "generation_pending": False,
    }

    with (
        patch("app.services.newspaper.is_newspaper_document", return_value=True),
        patch("app.services.learn_chat_context.get_progress", return_value={"current_page": 3}),
        patch("app.services.learn_chat_context.build_learn_queue_state", return_value=state),
        patch("app.services.learn_chat_context.page_range_bounds", return_value=(1, 20)),
        patch("app.services.learn_chat_context.assertion_page_number", return_value=15),
        patch(
            "app.services.learn_chat_context._assertion_mcq",
            return_value={
                "stem": "Who is Pat Cummins?",
                "options": ["A", "B"],
                "correct_index": 0,
            },
        ),
    ):
        block = build_learn_chat_context(
            db,
            doc_id,
            doc,
            scope={"current_assertion_id": "q-on-page-15"},
        )

    assert block is not None
    assert "The Hindu · 2026-07-26" in block
    assert "cooked from page 15" in block
    assert "Source page for this question: 15" in block
    assert "Who is Pat Cummins?" in block
