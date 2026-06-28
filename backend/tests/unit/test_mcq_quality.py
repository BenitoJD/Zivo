"""Unit tests for MCQ quality gate (IWF heuristics + critic loop)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.mcq_quality import (
    _critique_passes,
    _parse_mcq_blocks,
    generate_quality_mcq,
    has_fatal_heuristic_flaws,
    run_heuristic_checks,
)

def _good_mcq() -> dict:
    return {
        "question": "What role does chlorophyll play in photosynthesis?",
        "options": [
            "It absorbs light energy",
            "It stores glucose",
            "It fixes nitrogen",
            "It transports water",
        ],
        "correct_index": 0,
        "explanation": "Chlorophyll captures light for the reaction.",
    }


def test_good_mcq_passes_heuristics() -> None:
    flaws = run_heuristic_checks(_good_mcq())
    assert not has_fatal_heuristic_flaws(flaws)


def test_detects_none_of_the_above() -> None:
    mcq = {**_good_mcq(), "options": ["A", "B", "None of the above"]}
    flaws = run_heuristic_checks(mcq)
    codes = {f["code"] for f in flaws}
    assert "none_or_all_of_above" in codes
    assert has_fatal_heuristic_flaws(flaws)


def test_detects_longest_option_correct() -> None:
    mcq = {
        "question": "Which statement is true?",
        "options": ["Short", "Also short", "This is the correct answer with much more detail and explanation", "Tiny"],
        "correct_index": 2,
        "explanation": "Because.",
    }
    flaws = run_heuristic_checks(mcq)
    assert any(f["code"] == "longest_option_correct" for f in flaws)


def test_detects_negative_wording() -> None:
    mcq = {
        "question": "Which of the following is NOT a function of the nucleus?",
        "options": ["DNA storage", "Ribosome assembly", "Gene regulation", "Cell division control"],
        "correct_index": 1,
        "explanation": "Ribosomes assemble in the cytoplasm.",
    }
    flaws = run_heuristic_checks(mcq)
    assert any(f["code"] == "negative_wording" for f in flaws)


def test_detects_invalid_structure() -> None:
    flaws = run_heuristic_checks({"question": "", "options": [], "correct_index": 0})
    assert any(f["code"] == "invalid_structure" for f in flaws)


def test_detects_meta_page_reference_in_stem() -> None:
    mcq = {
        "question": "On page 12, what is photosynthesis?",
        "options": ["Energy capture", "Water transport", "Cell division", "Protein synthesis"],
        "correct_index": 0,
        "explanation": "Chlorophyll absorbs light.",
    }
    flaws = run_heuristic_checks(mcq)
    assert any(f["code"] == "meta_page_reference" for f in flaws)
    assert has_fatal_heuristic_flaws(flaws)


def test_detects_meta_page_reference_in_option() -> None:
    mcq = {
        "question": "What is photosynthesis?",
        "options": [
            "Energy capture",
            "As stated in the passage, water transport",
            "Cell division",
            "Protein synthesis",
        ],
        "correct_index": 0,
        "explanation": "Chlorophyll absorbs light.",
    }
    flaws = run_heuristic_checks(mcq)
    assert any(f["code"] == "meta_page_reference" for f in flaws)


# ---------------------------------------------------------------------------
# not_self_contained — questions must be answerable without the source document
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "stem",
    [
        "As shown in the figure, which layer contains most chloroplasts?",
        "In the diagram above, what is labeled X?",
        "Refer to the table above. Which value is highest?",
        "From the given text, what is the main cause of the revolt?",
        "What does the aforementioned case demonstrate?",
        "As described earlier, which reaction is exothermic?",
        "In the example shown, what is the output?",
    ],
)
def test_detects_not_self_contained_in_stem(stem: str) -> None:
    """Stems that dangle a reference only visible in the source must be flagged."""
    mcq = {
        "question": stem,
        "options": ["Option A", "Option B", "Option C", "Option D"],
        "correct_index": 0,
        "explanation": "Some explanation.",
    }
    flaws = run_heuristic_checks(mcq)
    assert any(f["code"] == "not_self_contained" for f in flaws)
    assert has_fatal_heuristic_flaws(flaws)


def test_detects_not_self_contained_in_option() -> None:
    mcq = {
        "question": "Which layer of a leaf contains the most chloroplasts?",
        "options": [
            "The palisade mesophyll",
            "As shown in the figure above, the epidermis",
            "The cuticle",
            "The vascular bundle",
        ],
        "correct_index": 0,
        "explanation": "Palisade mesophyll is packed with chloroplasts.",
    }
    flaws = run_heuristic_checks(mcq)
    assert any(f["code"] == "not_self_contained" for f in flaws)


def test_truly_standalone_question_passes() -> None:
    """A question with all facts inline and no dangling refs must pass cleanly."""
    mcq = {
        "question": "In the 1857 revolt against the British East India Company, what was a primary cause?",
        "options": [
            "Discontent over greased rifle cartridges",
            "A shortage of tea imports",
            "A royal succession dispute in France",
            "The invention of the telegraph",
        ],
        "correct_index": 0,
        "explanation": "The cartridge grease issue offended both Hindu and Muslim sepoys.",
    }
    flaws = run_heuristic_checks(mcq)
    codes = {f["code"] for f in flaws}
    assert "not_self_contained" not in codes
    assert "meta_page_reference" not in codes


def test_normalize_strips_explanation_meta_framing() -> None:
    from app.services.mcq_quality import _normalize_mcq_payload

    payload = _normalize_mcq_payload(
        {
            "question": "What is ATP?",
            "options": ["Energy carrier", "Structural protein", "Enzyme", "Lipid"],
            "correct_index": 0,
            "explanation": "The text states that ATP stores chemical energy.",
            "primary_concept_key": "atp",
        },
        {"key": "atp", "label": "ATP"},
    )
    assert "text states" not in payload["explanation"].lower()
    assert "ATP" in payload["explanation"]


def test_detects_duplicate_stem_vs_prior() -> None:
    mcq = _good_mcq()
    prior = [{"question": mcq["question"], "correct_answer": "other"}]
    flaws = run_heuristic_checks(mcq, prior_mcqs=prior)
    assert any(f["code"] == "too_similar_to_prior" for f in flaws)
    assert has_fatal_heuristic_flaws(flaws)


def test_critique_passes_requires_zero_fatal() -> None:
    assert _critique_passes({"pass": True, "flaw_count": 0, "fatal_flaws": []})
    assert not _critique_passes({"pass": True, "flaw_count": 0, "fatal_flaws": ["ambiguous_unclear"]})
    assert not _critique_passes({"pass": True, "flaw_count": 2, "fatal_flaws": []})
    assert not _critique_passes({"pass": False, "flaw_count": 0, "fatal_flaws": []})


def test_generate_quality_mcq_passes_on_first_attempt() -> None:
    db = MagicMock()
    good = _good_mcq()
    good["primary_concept_key"] = "chlorophyll"
    good["primary_concept"] = "Chlorophyll role"
    critic_json = json.dumps(
        {
            "pass": True,
            "flaw_count": 0,
            "fatal_flaws": [],
            "flaws": [],
            "cognitive_level": "comprehension",
            "matches_aspect": True,
            "provokes_understanding": True,
            "rewrite_hints": "",
        }
    )
    zv_block = f'```zv-mcq\n{json.dumps(good)}\n```'

    with patch("app.services.mcq_quality._complete_chat_sync", side_effect=[zv_block, critic_json]):
        result = generate_quality_mcq(
            db,
            page_text="Chlorophyll absorbs light during photosynthesis on page 3.",
            page_number=3,
            sequence=1,
            target_aspect={"key": "chlorophyll", "label": "Chlorophyll role", "cognitive_angle": "mechanism"},
        )

    assert result is not None
    assert result["quality"]["pass"] is True
    assert result["quality"]["attempts"] == 1
    assert result["cognitive_angle"] == "mechanism"


def test_generate_quality_mcq_rewrites_then_passes() -> None:
    db = MagicMock()
    # Stem trips the negative-wording heuristic ("which ... not"), which is a
    # fatal flaw. Fatal heuristic flaws skip the critic and go straight to a
    # rewrite on the next attempt. So the call sequence is: draft(bad) ->
    # fatal heuristic -> rewrite(good) -> clean -> return on attempt 2.
    bad = {
        "question": "Which of the following is NOT a stage of mitosis?",
        "options": ["Prophase", "Metaphase", "Anaphase", "Digestion"],
        "correct_index": 3,
        "explanation": "Digestion is not part of mitosis.",
    }
    good = _good_mcq()
    good["primary_concept_key"] = "mitosis"
    bad_block = f'```zv-mcq\n{json.dumps(bad)}\n```'
    good_block = f'```zv-mcq\n{json.dumps(good)}\n```'

    with (
        patch("app.services.mcq_quality._complete_chat_sync", side_effect=[bad_block, good_block]),
        # This test exercises the rewrite flow, not key verification.
        patch("app.services.mcq_quality.verify_answer_key", return_value=None),
    ):
        result = generate_quality_mcq(
            db,
            page_text="Mitosis divides the nucleus.",
            page_number=5,
            sequence=2,
            target_aspect={"key": "mitosis", "label": "Mitosis basics"},
            max_attempts=3,
        )

    assert result is not None
    assert result["quality"]["attempts"] == 2


def test_generate_quality_mcq_returns_none_when_exhausted() -> None:
    db = MagicMock()
    bad = {
        "question": "Which is NOT correct?",
        "options": ["A", "B", "None of the above"],
        "correct_index": 2,
        "explanation": "Nope.",
    }
    critic_fail = json.dumps(
        {
            "pass": False,
            "flaw_count": 3,
            "fatal_flaws": ["negative_wording"],
            "flaws": [],
            "rewrite_hints": "Fix it.",
        }
    )
    bad_block = f'```zv-mcq\n{json.dumps(bad)}\n```'

    with patch(
        "app.services.mcq_quality._complete_chat_sync",
        side_effect=[bad_block, critic_fail, bad_block, critic_fail, bad_block, critic_fail],
    ):
        result = generate_quality_mcq(
            db,
            page_text="Some page text.",
            page_number=1,
            sequence=1,
            max_attempts=3,
        )

    assert result is None


def test_generate_quality_mcq_empty_page_text() -> None:
    db = MagicMock()
    assert generate_quality_mcq(db, page_text="", page_number=1, sequence=1) is None


def test_generate_quality_mcq_embedding_gate_retries() -> None:
    db = MagicMock()
    similar = {
        "question": "How does chlorophyll function during photosynthesis?",
        "options": [
            "It absorbs light energy",
            "It stores glucose",
            "It fixes nitrogen",
            "It transports water",
        ],
        "correct_index": 0,
        "explanation": "Chlorophyll captures light.",
        "primary_concept_key": "chlorophyll",
    }
    distinct = {
        "question": "Where in the cell does the Calvin cycle occur?",
        "options": ["Stroma", "Thylakoid", "Nucleus", "Cytoplasm"],
        "correct_index": 0,
        "explanation": "Calvin cycle runs in the stroma.",
        "primary_concept_key": "calvin",
    }
    similar_block = f'```zv-mcq\n{json.dumps(similar)}\n```'
    distinct_block = f'```zv-mcq\n{json.dumps(distinct)}\n```'
    prior = [
        {
            "question": "What role does chlorophyll play in photosynthesis?",
            "correct_answer": "It absorbs light energy",
            "aspect_label": "Chlorophyll",
        }
    ]

    # Fast-path control flow: a clean draft skips the critic entirely. So the
    # call sequence is: draft(similar) -> embedding gate rejects -> rewrite
    # (distinct) -> embedding gate accepts. Two _complete_chat_sync calls, no
    # critic calls. The earlier 4-call chain desynced because it assumed the
    # critic ran on every draft (pre-deferred-critic behavior).
    with (
        patch("app.services.mcq_quality._complete_chat_sync", side_effect=[similar_block, distinct_block]),
        patch("app.services.mcq_quality.is_mcq_too_similar", side_effect=[(True, 0.95), (False, 0.4)]),
        # This test exercises the embedding gate, not key verification.
        patch("app.services.mcq_quality.verify_answer_key", return_value=None),
    ):
        result = generate_quality_mcq(
            db,
            page_text="Chlorophyll and Calvin cycle on page 3.",
            page_number=3,
            sequence=2,
            target_aspect={"key": "calvin", "label": "Calvin cycle"},
            prior_mcqs=prior,
            max_attempts=3,
        )

    assert result is not None
    assert result["question"].startswith("Where in the cell")
    assert result["quality"]["attempts"] == 2


# ---------------------------------------------------------------------------
# _parse_mcq_blocks — multi-block extraction for one-call batch generation
# ---------------------------------------------------------------------------


def test_parse_mcq_blocks_multiple_fenced() -> None:
    a = {"question": "What is A?", "options": ["1", "2"], "correct_index": 0}
    b = {"question": "What is B?", "options": ["3", "4"], "correct_index": 1}
    raw = f"Here are two questions:\n```zv-mcq\n{json.dumps(a)}\n```\n\n```zv-mcq\n{json.dumps(b)}\n```"
    parsed = _parse_mcq_blocks(raw)
    assert len(parsed) == 2
    assert parsed[0]["question"] == "What is A?"
    assert parsed[1]["correct_index"] == 1


def test_parse_mcq_blocks_single_fenced() -> None:
    a = {"question": "Solo?", "options": ["x", "y"], "correct_index": 0}
    raw = f"```zv-mcq\n{json.dumps(a)}\n```"
    parsed = _parse_mcq_blocks(raw)
    assert len(parsed) == 1
    assert parsed[0]["question"] == "Solo?"


def test_parse_mcq_blocks_json_array_fallback() -> None:
    a = {"question": "Q1?", "options": ["a", "b"], "correct_index": 0}
    b = {"question": "Q2?", "options": ["c", "d"], "correct_index": 0}
    raw = json.dumps([a, b])
    parsed = _parse_mcq_blocks(raw)
    assert len(parsed) == 2
    assert parsed[1]["question"] == "Q2?"


def test_parse_mcq_blocks_skips_malformed_block() -> None:
    good = {"question": "Valid?", "options": ["x", "y"], "correct_index": 0}
    raw = (
        "```zv-mcq\n{not valid json}\n```\n\n"
        f"```zv-mcq\n{json.dumps(good)}\n```"
    )
    parsed = _parse_mcq_blocks(raw)
    assert len(parsed) == 1
    assert parsed[0]["question"] == "Valid?"


def test_parse_mcq_blocks_empty_input() -> None:
    assert _parse_mcq_blocks("") == []
    assert _parse_mcq_blocks("   ") == []


def test_parse_mcq_blocks_bare_objects_without_fences() -> None:
    a = {"question": "Bare1?", "options": ["a", "b"], "correct_index": 0}
    b = {"question": "Bare2?", "options": ["c", "d"], "correct_index": 1}
    raw = f"Here:\n{json.dumps(a)}\nand\n{json.dumps(b)}"
    parsed = _parse_mcq_blocks(raw)
    assert len(parsed) == 2
    assert parsed[0]["question"] == "Bare1?"
    assert parsed[1]["correct_index"] == 1
