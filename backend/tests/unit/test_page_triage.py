"""Page triage graph — budget, aspects, JSON parsing."""

from __future__ import annotations

from unittest.mock import patch

from app.graphs.page_triage_graph import (
    _fallback_triage,
    _finalize_triage,
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
    assert result["question_budget"] >= 1
    assert len(result["aspects"]) >= 1
    assert result["aspects"][0]["label"]


def test_fallback_triage_empty_page_yields_zero() -> None:
    # No floor: an empty page honestly yields zero questions.
    result = _fallback_triage("", page_number=1)
    assert result["question_budget"] == 0
    assert len(result["aspects"]) == 0


def test_finalize_triage_shrinks_budget_after_dedup() -> None:
    aspects = [
        {"key": "a", "label": "chlorophyll absorption", "asked": False, "answered": False},
        {"key": "b", "label": "chlorophyll light wavelengths", "asked": False, "answered": False},
        {"key": "c", "label": "Calvin cycle", "asked": False, "answered": False},
    ]

    def fake_embed(texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            if "calvin" in t.lower():
                out.append([0.0, 1.0])
            else:
                out.append([1.0, 0.0])
        return out

    with patch("app.services.mcq_dedup.embed_texts", side_effect=fake_embed):
        result = _finalize_triage(aspects=aspects, budget=3, rationale="test")

    assert result["question_budget"] == 2
    assert len(result["aspects"]) == 2
    assert result["aspect_dedup"]["deduped_count"] == 2
