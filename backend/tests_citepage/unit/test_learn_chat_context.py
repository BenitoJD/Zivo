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
        patch("app.services.learn_chat_context.selected_page_list", return_value=[25, 26, 27]),
        patch("app.services.learn_chat_context.chat_rag_window", return_value=[25, 26]),
        patch(
            "app.services.learn_chat_context._assertion_mcq",
            return_value={
                "stem": "What role did printing play?",
                "options": ["Option A", "Option B", "Option C"],
                "correct_index": 1,
            },
        ),
    ):
        block = build_learn_chat_context(db, doc_id, doc)

    assert block is not None
    assert "Question 7 of 15 on page 25" in block
    assert "What role did printing play?" in block
    assert "A. Option A" in block
    assert "primary focus" in block.lower()
    assert "supplementary" in block.lower()
    assert "authoritative" in block.lower()


def test_build_learn_chat_context_includes_confirmed_answer() -> None:
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.meta = {"question_pool_initialized": True, "selected_range": {"from": 1, "to": 3}}
    db = MagicMock()

    state = {
        "current_page": 1,
        "question_number": 1,
        "question_budget": 5,
        "questions_answered": 0,
        "questions_generated": 1,
        "current_assertion_id": "q1",
        "page_complete": False,
        "document_complete": False,
        "generation_pending": False,
    }

    with (
        patch(
            "app.services.learn_chat_context.get_progress",
            return_value={
                "current_page": 1,
                "last_confirmed_answer": {
                    "assertion_id": "q1",
                    "choice_index": 2,
                    "correct": False,
                },
            },
        ),
        patch("app.services.learn_chat_context.build_learn_queue_state", return_value=state),
        patch("app.services.learn_chat_context.page_range_bounds", return_value=(1, 3)),
        patch("app.services.learn_chat_context.selected_page_list", return_value=[1, 2, 3]),
        patch("app.services.learn_chat_context.chat_rag_window", return_value=[1, 2]),
        patch(
            "app.services.learn_chat_context._assertion_mcq",
            return_value={
                "stem": "Which is true?",
                "options": ["One", "Two", "Three"],
                "correct_index": 0,
            },
        ),
    ):
        block = build_learn_chat_context(db, doc_id, doc)

    assert block is not None
    assert "Learner confirmed answer: C (Three) — incorrect" in block


def test_build_learn_chat_context_none_when_pool_not_initialized() -> None:
    doc = MagicMock()
    doc.meta = {}
    assert build_learn_chat_context(MagicMock(), uuid.uuid4(), doc) is None


def test_learn_scope_fields_for_cache_key() -> None:
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.meta = {"question_pool_initialized": True}

    with (
        patch(
            "app.services.learn_chat_context.get_progress",
            return_value={
                "last_confirmed_answer": {
                    "assertion_id": "x",
                    "choice_index": 1,
                    "correct": True,
                }
            },
        ),
        patch(
            "app.services.learn_chat_context.build_learn_queue_state",
            return_value={"question_number": 4, "current_assertion_id": "x"},
        ),
    ):
        fields = learn_scope_fields(MagicMock(), doc_id, doc)

    assert fields == {
        "question_number": 4,
        "current_assertion_id": "x",
        "confirmed_choice_index": 1,
        "answer_correct": True,
    }
