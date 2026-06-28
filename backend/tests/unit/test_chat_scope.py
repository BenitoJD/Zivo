"""Chat scope normalization and retrieval gate."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services.chat_scope import normalize_chat_scope
from app.services.retrieval_gate import is_conversational_followup, needs_retrieval


def test_normalize_chat_scope_sets_page_window() -> None:
    doc = MagicMock()
    doc.meta = {"page_count": 200, "selected_range": {"from": 1, "to": 200}}
    scope = normalize_chat_scope({"current_page": 10}, doc)
    assert scope["page_start"] == 7
    assert scope["page_end"] == 12
    assert scope["current_page"] == 10


def test_normalize_chat_scope_early_page() -> None:
    doc = MagicMock()
    doc.meta = {"selected_range": {"from": 1, "to": 100}}
    scope = normalize_chat_scope({"current_page": 1}, doc)
    assert scope["page_start"] == 1
    assert scope["page_end"] == 2


def test_needs_retrieval_when_current_page_set() -> None:
    assert needs_retrieval("hi", scope={"current_page": 8}, has_history=True) is True


def test_is_conversational_followup_detects_acknowledgements_and_nudges() -> None:
    assert is_conversational_followup("thanks")
    assert is_conversational_followup("got it!")
    assert is_conversational_followup("go on")
    assert is_conversational_followup("explain that again")
    assert is_conversational_followup("say that differently?")


def test_is_conversational_followup_false_for_real_questions() -> None:
    # Content questions must still be treated as such (e.g. quiz guardrail still applies).
    assert not is_conversational_followup("What is photosynthesis?")
    assert not is_conversational_followup("Explain the difference between mitosis and meiosis")
    assert not is_conversational_followup("Why does a catalyst speed up a reaction?")

