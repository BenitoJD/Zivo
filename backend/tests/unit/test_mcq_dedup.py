"""Unit tests for per-page MCQ dedup helpers."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.mcq_dedup import (
    cosine_similarity,
    dedupe_aspects,
    coerce_mcq_options,
    format_prior_mcqs_block,
    has_document_meta_reference,
    has_page_reference_stem,
    is_mcq_too_similar,
    normalize_stem,
    prior_mcq_from_payload,
    sanitize_mcq_explanation,
    sanitize_mcq_option,
    sanitize_mcq_stem,
    stems_match,
)


def test_cosine_similarity_identical() -> None:
    vec = [1.0, 0.0, 1.0]
    assert cosine_similarity(vec, vec) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_normalize_stem_strips_punctuation() -> None:
    assert normalize_stem("Which wavelengths?") == normalize_stem("which wavelengths")


def test_stems_match() -> None:
    assert stems_match("What is photosynthesis?", "what is photosynthesis")


def test_format_prior_mcqs_block_empty() -> None:
    assert format_prior_mcqs_block([]) == ""


def test_format_prior_mcqs_block_lists_prior() -> None:
    block = format_prior_mcqs_block(
        [{"aspect_label": "Chlorophyll", "question": "Q?", "correct_answer": "A"}]
    )
    assert "Chlorophyll" in block
    assert "do NOT repeat" in block


def test_prior_mcq_from_payload_dict_options() -> None:
    prior = prior_mcq_from_payload(
        {
            "question": "Which organelle?",
            "options": {"0": "Nucleus", "1": "Ribosome", "2": "Golgi"},
            "correct_index": 0,
            "primary_concept_key": "organelles",
        }
    )
    assert prior["correct_answer"] == "Nucleus"
    assert prior["question"] == "Which organelle?"


def test_sanitize_mcq_option_strips_letter_prefix() -> None:
    assert sanitize_mcq_option("A) European accounts were printed") == "European accounts were printed"
    assert sanitize_mcq_option("B. Only in France") == "Only in France"


def test_sanitize_mcq_stem_strips_page_framing() -> None:
    stem = "According to the page, how did publication differ?"
    assert sanitize_mcq_stem(stem) == "How did publication differ?"
    assert has_page_reference_stem(stem) is True
    assert has_document_meta_reference(stem) is True
    assert has_document_meta_reference("How did publication differ?") is False


def test_sanitize_mcq_stem_preserves_newlines_for_statement_items() -> None:
    # Statement/matching/ordering/code stems carry meaningful line breaks the UI
    # renders — the sanitizer must collapse horizontal whitespace WITHOUT
    # flattening newlines.
    stem = (
        "Consider the following statements:\n"
        "1.  A catalyst lowers   activation energy.\n"
        "2. A catalyst is consumed.\n"
        "Which of the statements given above is/are correct?"
    )
    out = sanitize_mcq_stem(stem)
    assert out.count("\n") == 3
    assert "1. A catalyst lowers activation energy." in out
    assert out.startswith("Consider the following statements:")


def test_detects_exam_forbidden_mid_stem_refs() -> None:
    assert has_document_meta_reference("On page 12, what is photosynthesis?")
    assert has_document_meta_reference("In this book, how did Bernier describe the court?")
    assert has_document_meta_reference("What does the passage say about groundwater?")
    assert not has_document_meta_reference("What is the primary function of chlorophyll?")


@pytest.mark.parametrize(
    "stem",
    [
        "As mentioned above, which reaction is exothermic?",
        "Refer to the figure above. Which part is labeled X?",
        "According to the given text, what is the main cause?",
        "In the provided passage, who led the revolt?",
        "As shown earlier, what does the diagram depict?",
        "What does the aforementioned case demonstrate?",
        "As noted in the reading, where is the Calvin cycle located?",
    ],
)
def test_detects_widened_exam_forbidden_refs(stem: str) -> None:
    """Residual source-pointing phrasings must all be caught after the net was widened."""
    assert has_document_meta_reference(stem), f"missed forbidden framing: {stem!r}"


def test_clean_standalone_stem_is_not_flagged() -> None:
    clean = "Which source supplies most cities with drinking water?"
    assert not has_document_meta_reference(clean)
    assert sanitize_mcq_stem(clean) == clean


def test_sanitize_mcq_stem_strips_on_page_prefix() -> None:
    assert sanitize_mcq_stem("On page 12, what is photosynthesis?") == "What is photosynthesis?"


def test_sanitize_mcq_explanation_strips_text_framing() -> None:
    raw = "The text states that catalysts lower activation energy."
    cleaned = sanitize_mcq_explanation(raw)
    assert "text states" not in cleaned.lower()
    assert "activation energy" in cleaned.lower()


def test_format_prior_mcqs_block_uses_exam_neutral_label() -> None:
    block = format_prior_mcqs_block(
        [{"aspect_label": "Chlorophyll", "question": "Q?", "correct_answer": "A"}]
    )
    assert "already used" in block
    assert "on this page" not in block


def test_coerce_mcq_options_strips_prefixes() -> None:
    assert coerce_mcq_options(["A) One", "B) Two"]) == ["One", "Two"]


def test_dedupe_aspects_clusters_near_duplicates() -> None:
    aspects = [
        {"key": "a", "label": "chlorophyll light absorption"},
        {"key": "b", "label": "chlorophyll absorbs light wavelengths"},
        {"key": "c", "label": "Calvin cycle location"},
    ]

    def fake_embed(texts: list[str]) -> list[list[float]]:
        # First two texts map to nearly identical vectors; third is different.
        out: list[list[float]] = []
        for t in texts:
            if "calvin" in t.lower():
                out.append([0.0, 1.0])
            else:
                out.append([1.0, 0.0])
        return out

    with patch("app.services.mcq_dedup.embed_texts", side_effect=fake_embed):
        deduped, meta = dedupe_aspects(aspects, threshold=0.99)

    assert len(deduped) == 2
    assert meta["raw_count"] == 3
    assert meta["deduped_count"] == 2
    assert meta["merged_keys"]


def test_is_mcq_too_similar_detects_high_cosine() -> None:
    mcq = {"question": "What is ATP?", "options": ["Energy", "Water"], "correct_index": 0}
    prior = [{"question": "What is ATP used for?", "correct_answer": "Energy carrier"}]

    def fake_embed(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    with patch("app.services.mcq_dedup.embed_texts", side_effect=fake_embed):
        too_similar, max_sim = is_mcq_too_similar(mcq, prior, threshold=0.92)

    assert too_similar is True
    assert max_sim == 1.0
