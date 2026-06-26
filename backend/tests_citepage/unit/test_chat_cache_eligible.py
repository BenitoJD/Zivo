"""Semantic response cache must not serve follow-up turns.

The cache matches a new message to a stored reply by embedding similarity
alone — it has no awareness of conversation history. So a follow-up ("no, I
meant…", "go deeper") could otherwise be answered with the *original* cached
reply. Cache hits are restricted to fresh, standalone first turns.
"""

from __future__ import annotations

from app.api.chat import _cache_eligible


def _base(**overrides: bool) -> bool:
    args = dict(
        include_image=False,
        selection_text=None,
        has_citations=True,
        doc_count=1,
        has_history=False,
    )
    args.update(overrides)
    return _cache_eligible(**args)  # type: ignore[arg-type]


def test_fresh_standalone_turn_with_citations_is_eligible() -> None:
    assert _base() is True


def test_followup_turn_with_history_is_never_eligible() -> None:
    # The main fix: once there's a back-and-forth, always call the model.
    assert _base(has_history=True) is False
    assert _base(has_history=True, has_citations=True, doc_count=1) is False


def test_multimodal_turn_is_not_eligible() -> None:
    assert _base(include_image=True) is False


def test_turn_with_selection_text_is_not_eligible() -> None:
    assert _base(selection_text="some highlighted passage") is False


def test_turn_without_citations_is_not_eligible() -> None:
    assert _base(has_citations=False) is False


def test_multi_document_mention_turn_is_not_eligible() -> None:
    assert _base(doc_count=2) is False
