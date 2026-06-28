"""Page triage graph — budget, aspects, JSON parsing."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.graphs import page_triage_graph as ptg
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


def test_fallback_triage_empty_page_is_non_content() -> None:
    # No floor: an empty page yields zero — and must be flagged non_content so the
    # page completes (a 0-budget page that isn't non_content strands the learner).
    result = _fallback_triage("", page_number=1)
    assert result["question_budget"] == 0
    assert len(result["aspects"]) == 0
    assert result["non_content"] is True


def test_fallback_triage_never_persists_the_strand_shape() -> None:
    # Contract pinning the strand fix: a fallback result must NEVER be the dead
    # shape (zero aspects AND not non_content), for any input — content pages get
    # aspects, contentless pages get non_content=True. Either way the page resolves.
    for text in ["", "   \n\t ", "word " * 600, "para one\n\npara two\n\npara three"]:
        r = _fallback_triage(text, page_number=7)
        has_aspects = len(r["aspects"]) > 0
        assert has_aspects or r["non_content"] is True, f"strand shape for {text!r}"


def test_run_page_triage_defers_unindexed_page_instead_of_non_content() -> None:
    # The indexing race: eager lookahead triage reaches a page before its text is
    # ingested. With no chunks, triage must DEFER (write no coverage) — never
    # persist a false non_content verdict that would auto-skip a real page and
    # prematurely complete the whole document.
    db = MagicMock()
    with (
        patch.object(ptg, "fetch_chunks_for_page_range", return_value=[]),
        patch("app.services.rag_window.indexed_pages_for_document", return_value=set()),
        patch.object(ptg, "save_page_coverage") as save_cov,
    ):
        result = ptg.run_page_triage(db, uuid.uuid4(), page_number=12, precompute=True)

    assert result.get("deferred") is True
    assert result["non_content"] is False
    save_cov.assert_not_called()


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
