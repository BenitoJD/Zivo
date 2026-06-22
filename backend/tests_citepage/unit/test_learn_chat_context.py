"""Learn session context injected into tutor chat."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.services.learn_chat_context import build_learn_chat_context, learn_scope_fields


def test_build_learn_chat_context_includes_position_and_stem() -> None:
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.meta = {"question_pool_initialized": True, "selected_range": {"from": 25, "to": 33}}
    db = MagicMock()

    state = {
        "current_page": 25,
        "question_number": 7,
        "question_budget": 15,
        "questions_answered": 6,
        "questions_generated": 7,
        "current_assertion_id": "a1b2",
        "page_complete": False,
        "document_complete": False,
        "generation_pending": False,
    }

    with (
        patch("app.services.learn_chat_context.get_progress", return_value={"current_page": 25}),
        patch("app.services.learn_chat_context.build_learn_queue_state", return_value=state),
        patch("app.services.learn_chat_context.page_range_bounds", return_value=(25, 33)),
        patch(
            "app.services.learn_chat_context._assertion_question",
            return_value="What role did printing play?",
        ),
    ):
        block = build_learn_chat_context(db, doc_id, doc)

    assert block is not None
    assert "Question 7 of 15 on page 25" in block
    assert "What role did printing play?" in block
    assert "authoritative" in block.lower()


def test_build_learn_chat_context_none_when_pool_not_initialized() -> None:
    doc = MagicMock()
    doc.meta = {}
    assert build_learn_chat_context(MagicMock(), uuid.uuid4(), doc) is None


def test_learn_scope_fields_for_cache_key() -> None:
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.meta = {"question_pool_initialized": True}

    with (
        patch("app.services.learn_chat_context.get_progress", return_value={}),
        patch(
            "app.services.learn_chat_context.build_learn_queue_state",
            return_value={"question_number": 4, "current_assertion_id": "x"},
        ),
    ):
        fields = learn_scope_fields(MagicMock(), doc_id, doc)

    assert fields == {"question_number": 4, "current_assertion_id": "x"}
