"""Unit tests for free dictionary + Wikipedia lookups."""

from __future__ import annotations

import asyncio

import pytest

from app.services.reference_lookup import (
    ReferenceLookupError,
    _dictionary_token,
    lookup_dictionary,
    lookup_wikipedia_summary,
)


def test_dictionary_token_uses_first_word() -> None:
    assert _dictionary_token("Siwan police") == "Siwan"
    assert _dictionary_token("  hello   world ") == "hello"


def test_lookup_dictionary_returns_definition() -> None:
    """Live upstream (dictionaryapi.dev). On an unreachable network the service
    must still fail with the domain error, never leak raw httpx exceptions."""
    try:
        entry = asyncio.run(lookup_dictionary("hello"))
        assert entry.word.lower() == "hello"
        assert entry.definition
        assert entry.part_of_speech
    except ReferenceLookupError:
        pytest.skip("dictionaryapi.dev unreachable from this network")


def test_lookup_wikipedia_summary_returns_extract() -> None:
    summary = asyncio.run(lookup_wikipedia_summary("Photosynthesis"))
    assert summary.title
    assert "Photosynthesis" in summary.title or "photosynthesis" in summary.title.lower()
    assert len(summary.extract) > 40
    assert summary.source_url.startswith("http")


def test_lookup_wikipedia_summary_siwan_falls_through_name_hit() -> None:
    summary = asyncio.run(lookup_wikipedia_summary("Siwan"))
    assert summary.title
    assert len(summary.extract) > 40
    assert "siwan" in summary.title.lower()
