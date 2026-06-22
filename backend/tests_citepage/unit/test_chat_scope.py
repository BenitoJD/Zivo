"""Chat scope normalization and retrieval gate."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services.chat_scope import normalize_chat_scope
from app.services.retrieval_gate import needs_retrieval


def test_normalize_chat_scope_sets_page_window() -> None:
    doc = MagicMock()
    doc.meta = {"page_count": 200}
    scope = normalize_chat_scope({"current_page": 10}, doc)
    assert scope["page_start"] == 9
    assert scope["page_end"] == 11
    assert scope["current_page"] == 10


def test_normalize_chat_scope_clamps_to_page_count() -> None:
    doc = MagicMock()
    doc.meta = {"page_count": 10}
    scope = normalize_chat_scope({"current_page": 10}, doc)
    assert scope["page_start"] == 9
    assert scope["page_end"] == 10


def test_needs_retrieval_when_current_page_set() -> None:
    assert needs_retrieval("hi", scope={"current_page": 8}, has_history=True) is True
