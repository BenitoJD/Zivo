"""Page triage graph — budget, aspects, JSON parsing."""

from __future__ import annotations

from app.graphs.page_triage_graph import (
    _fallback_triage,
    _normalize_aspect,
    _parse_triage_json,
)


def test_parse_triage_json_from_fence() -> None:
    raw = '```json\n{"question_budget": 12, "aspects": [{"key": "a", "label": "Alpha"}]}\n```'
    parsed = _parse_triage_json(raw)
    assert parsed is not None
    assert parsed["question_budget"] == 12
    assert parsed["aspects"][0]["label"] == "Alpha"


def test_normalize_aspect_from_string() -> None:
    aspect = _normalize_aspect("Crown ownership of land", 1, 34)
    assert aspect is not None
    assert aspect["key"] == "crown-ownership-of-land"
    assert aspect["asked"] is False


def test_fallback_triage_dense_page() -> None:
    text = " ".join(["word"] * 600)
    result = _fallback_triage(text, page_number=34)
    assert result["question_budget"] >= 5
    assert len(result["aspects"]) >= 1
    assert result["aspects"][0]["label"]


def test_fallback_triage_sparse_page() -> None:
    result = _fallback_triage("", page_number=1)
    assert result["question_budget"] == 5
    assert len(result["aspects"]) == 1
