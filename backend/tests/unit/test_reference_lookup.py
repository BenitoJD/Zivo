"""Unit tests for free dictionary + Wikipedia lookups."""

from __future__ import annotations

import pytest

from app.services.reference_lookup import (
    _dictionary_token,
    lookup_dictionary,
    lookup_wikipedia_summary,
)


def test_dictionary_token_uses_first_word() -> None:
    assert _dictionary_token("Siwan police") == "Siwan"
    assert _dictionary_token("  hello   world ") == "hello"


@pytest.mark.asyncio
async def test_lookup_dictionary_returns_definition() -> None:
    entry = await lookup_dictionary("hello")
    assert entry.word.lower() == "hello"
    assert entry.definition
    assert entry.part_of_speech


@pytest.mark.asyncio
async def test_lookup_wikipedia_summary_returns_extract() -> None:
    summary = await lookup_wikipedia_summary("Photosynthesis")
    assert summary.title
    assert "Photosynthesis" in summary.title or "photosynthesis" in summary.title.lower()
    assert len(summary.extract) > 40
    assert summary.source_url.startswith("http")
