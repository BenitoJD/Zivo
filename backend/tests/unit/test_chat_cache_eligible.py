"""Unit tests for chat semantic-cache eligibility.

Cache may hit with history when scope includes history_digest, but never for
conversational follow-ups ("go deeper", "thanks") that need the live model.
"""

from __future__ import annotations

from study_api.chat import _cache_eligible


def _base(**overrides: object) -> bool:
    args: dict[str, object] = dict(
        include_image=False,
        selection_text=None,
        has_citations=True,
        doc_count=1,
        has_history=False,
        conversational_followup=False,
    )
    args.update(overrides)
    return _cache_eligible(**args)  # type: ignore[arg-type]


def test_fresh_standalone_turn_with_citations_is_eligible() -> None:
    assert _base() is True


def test_followup_with_history_is_eligible_when_not_conversational() -> None:
    assert _base(has_history=True, conversational_followup=False) is True


def test_conversational_followup_with_history_is_never_eligible() -> None:
    assert _base(has_history=True, conversational_followup=True) is False


def test_multimodal_turn_is_not_eligible() -> None:
    assert _base(include_image=True) is False


def test_turn_with_selection_text_is_not_eligible() -> None:
    assert _base(selection_text="some highlighted passage") is False


def test_turn_without_citations_is_not_eligible() -> None:
    assert _base(has_citations=False) is False


def test_multi_document_mention_turn_is_not_eligible() -> None:
    assert _base(doc_count=2) is False


def test_prefetched_reference_turn_is_not_eligible() -> None:
    assert _base(prefetched_reference=True) is False
