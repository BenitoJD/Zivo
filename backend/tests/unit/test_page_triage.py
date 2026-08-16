"""Page triage graph — budget, aspects, JSON parsing."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.engine_runtime import pick
from app.graphs import page_triage_graph as ptg
from app.graphs.page_triage_graph import (
    _fallback_triage,
    _finalize_triage,
    _normalize_aspect,
    _parse_triage_json,
)
from app.services.aspect_discovery import AspectDedupeVerdict


def _identity_dedupe(aspects, **_kwargs) -> AspectDedupeVerdict:
    return AspectDedupeVerdict(
        aspects=tuple(aspects),
        raw_count=len(aspects),
        deduped_count=len(aspects),
        merged_keys=(),
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


def test_fallback_triage_short_fragments_use_word_density() -> None:
    # DOCX/PDF line-break soup: hundreds of tiny blocks must NOT each become an MCQ.
    text = "\n\n".join(f"Short {i}." for i in range(200))
    words = len(text.split())

    def identity_dedupe(aspects, **_kwargs):
        return _identity_dedupe(aspects)

    with patch("app.graphs.page_triage_graph.dedupe_aspects", side_effect=identity_dedupe):
        result = _fallback_triage(text, page_number=9)
    assert result["question_budget"] == max(1, words // 120)
    assert len(result["aspects"]) == result["question_budget"]
    assert result.get("non_content") is not True


def test_fallback_triage_substantial_paragraphs_capped_by_words() -> None:
    # Real paragraphs count as ideas, but never beyond ~one per 120 words.
    text = "\n\n".join(
        (
            f"Unique concept {i} about topic number {i} with enough words here "
            f"to count as a substantial paragraph for heuristic triage."
        )
        for i in range(40)
    )
    words = len(text.split())

    def identity_dedupe(aspects: list) -> AspectDedupeVerdict:
        return _identity_dedupe(aspects)

    with patch("app.graphs.page_triage_graph.dedupe_aspects", side_effect=identity_dedupe):
        result = _fallback_triage(text, page_number=9)
    assert result["question_budget"] == min(40, words // 120)
    assert len(result["aspects"]) == result["question_budget"]
    assert result.get("non_content") is not True


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
        patch("app.services.rag_window.pages_ready_for_document", return_value=set()),
        patch.object(ptg, "save_page_coverage") as save_cov,
    ):
        result = ptg.run_page_triage(db, uuid.uuid4(), page_number=12, precompute=True)

    assert result.get("deferred") is True
    assert result["non_content"] is False
    save_cov.assert_not_called()


def test_run_page_triage_empty_ingested_blank_vision_is_non_content() -> None:
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    db = MagicMock()
    db.get.return_value = doc
    judge = MagicMock(return_value={"usable": False, "rationale": "Blank page."})

    with (
        patch.object(ptg, "fetch_chunks_for_page_range", return_value=[]),
        patch("app.services.rag_window.pages_ready_for_document", return_value={7}),
        patch("app.services.vision.judge_page_has_content", judge),
        patch("app.services.question_pool.should_skip_empty_page_vision", return_value=False),
        patch("app.services.question_pool.note_empty_page_triage") as note,
        patch.object(ptg, "save_page_coverage") as save_cov,
        patch.object(ptg, "on_triage_completed"),
    ):
        result = ptg.run_page_triage(db, doc_id, page_number=7)

    assert result["non_content"] is True
    assert result["question_budget"] == 0
    judge.assert_called_once()
    note.assert_called_once_with(db, doc_id, vision_usable=False)
    save_cov.assert_called_once()
    assert save_cov.call_args.kwargs["non_content"] is True


def test_run_page_triage_empty_ingested_usable_vision_asks_human() -> None:
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    db = MagicMock()
    db.get.return_value = doc
    judge = MagicMock(return_value={"usable": True, "rationale": "Diagram-heavy slide."})

    with (
        patch.object(ptg, "fetch_chunks_for_page_range", return_value=[]),
        patch("app.services.rag_window.pages_ready_for_document", return_value={3}),
        patch("app.services.vision.judge_page_has_content", judge),
        patch("app.services.question_pool.should_skip_empty_page_vision", return_value=False),
        patch("app.services.question_pool.note_empty_page_triage") as note,
        patch.object(ptg, "save_page_coverage"),
        patch.object(ptg, "on_triage_completed"),
    ):
        result = ptg.run_page_triage(db, doc_id, page_number=3)

    assert result["non_content"] is True
    note.assert_called_once_with(db, doc_id, vision_usable=True)
    judge.assert_called_once()


def test_run_page_triage_skips_vision_after_reselect_prompt() -> None:
    doc_id = uuid.uuid4()
    doc = MagicMock()
    doc.id = doc_id
    db = MagicMock()
    db.get.return_value = doc
    judge = MagicMock()

    with (
        patch.object(ptg, "fetch_chunks_for_page_range", return_value=[]),
        patch("app.services.rag_window.pages_ready_for_document", return_value={9}),
        patch("app.services.vision.judge_page_has_content", judge),
        patch("app.services.question_pool.should_skip_empty_page_vision", return_value=True),
        patch("app.services.question_pool.note_empty_page_triage") as note,
        patch.object(ptg, "save_page_coverage"),
        patch.object(ptg, "on_triage_completed"),
    ):
        result = ptg.run_page_triage(db, doc_id, page_number=9)

    assert result["non_content"] is True
    judge.assert_not_called()
    note.assert_not_called()


def test_finalize_triage_shrinks_budget_after_dedup() -> None:
    aspects = [
        {"key": "a", "label": "chlorophyll absorption", "asked": False, "answered": False},
        {"key": "b", "label": "chlorophyll light wavelengths", "asked": False, "answered": False},
        {"key": "c", "label": "Calvin cycle", "asked": False, "answered": False},
    ]

    def fake_embed(texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            out.append(pick("calvin" in t.lower(), lambda: [0.0, 1.0], lambda: [1.0, 0.0]))
        return out

    with patch("app.services.mcq_dedup.embed_texts", side_effect=fake_embed):
        result = _finalize_triage(aspects=aspects, rationale="test")

    # Planner owns N: 2 unique central units → learn N_page=2 (not raw LLM yield).
    assert result["question_budget"] == 2
    assert len(result["aspects"]) == 2
    assert result["aspect_dedup"]["deduped_count"] == 2
    assert result["budget_version"] == "qb.budget.v1"
    assert result["budget_confidence"] == "high"


def test_finalize_triage_ignores_raw_budget_uses_weights() -> None:
    aspects = [
        {"key": "a", "label": "A", "centrality": "central", "asked": False, "answered": False},
        {"key": "b", "label": "B", "centrality": "support", "asked": False, "answered": False},
        {"key": "c", "label": "C", "centrality": "skip", "asked": False, "answered": False},
    ]

    with patch("app.graphs.page_triage_graph.dedupe_aspects", side_effect=_identity_dedupe):
        result = _finalize_triage(aspects=aspects, rationale="weighted")

    # 1 + 0.5 + 0 → 1.5 → 2; skip dropped from cook list
    assert result["question_budget"] == 2
    assert [a["key"] for a in result["aspects"]] == ["a", "b"]
    assert result["n_cov"] == 1.5


def test_finalize_triage_support_only_is_honest_zero() -> None:
    aspects = [
        {"key": "a", "label": "Sidebar", "centrality": "support", "asked": False, "answered": False},
    ]

    with patch("app.graphs.page_triage_graph.dedupe_aspects", side_effect=_identity_dedupe):
        result = _finalize_triage(aspects=aspects, rationale="thin")

    # round(0.5)=0 → non_content, not filler
    assert result["question_budget"] == 0
    assert result["non_content"] is True


def test_finalize_triage_peripheral_maps_to_support() -> None:
    aspects = [
        {"key": "a", "label": "Main", "centrality": "central", "asked": False, "answered": False},
        {"key": "b", "label": "Aside", "centrality": "peripheral", "asked": False, "answered": False},
    ]

    with patch("app.graphs.page_triage_graph.dedupe_aspects", side_effect=_identity_dedupe):
        result = _finalize_triage(aspects=aspects, rationale="peripheral")

    by_key = {a["key"]: a for a in result["aspects"]}
    assert by_key["b"]["centrality"] == "support"
    assert by_key["b"]["central"] is False
    assert by_key["a"]["centrality"] == "central"


def test_triage_page_skips_llm_when_flag_off() -> None:
    settings = MagicMock()
    settings.llm_page_triage = False
    settings.allow_zero_questions = False
    text = "First idea here.\n\nSecond distinct idea.\n\nThird idea on the page."
    db = MagicMock()
    with (
        patch("app.config.get_settings", return_value=settings),
        patch.object(ptg, "_complete_chat_sync") as llm,
        patch("app.services.generation_cache.get", return_value=None),
        patch("app.services.generation_cache.put"),
    ):
        result = ptg._triage_page(db, page_text=text, page_number=3)

    llm.assert_not_called()
    assert result["question_budget"] >= 1
    assert len(result["aspects"]) >= 1
    assert result.get("non_content") is not True
