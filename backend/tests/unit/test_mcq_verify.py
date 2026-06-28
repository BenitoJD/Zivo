"""Independent answer-key verifier ("solve-back") — the correctness guarantee.

A blind solver re-answers each item from the source without seeing the marked
key; a confident disagreement is fatal. These tests pin that behaviour and its
conservative failure mode (a hiccuping verifier must not reject a good item).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.mcq_heuristics import FATAL_FLAW_CODES
from app.services.mcq_quality import verify_answer_key


def _mcq(correct_index: int = 0) -> dict:
    return {
        "question": "What captures light energy in photosynthesis?",
        "options": ["Chlorophyll", "Glucose", "Nitrogen", "Water"],
        "correct_index": correct_index,
    }


def _verify(verifier_response: str, mcq: dict) -> dict | None:
    db = MagicMock()
    with (
        patch("app.services.mcq_quality.get_prompt", return_value="prompt"),
        patch("app.services.mcq_quality._complete_chat_sync", return_value=verifier_response),
    ):
        return verify_answer_key(db, mcq=mcq, page_text="Chlorophyll absorbs light.", model_id=None)


def test_wrong_answer_key_is_registered_fatal() -> None:
    assert "wrong_answer_key" in FATAL_FLAW_CODES


def test_agreement_passes() -> None:
    resp = '{"answer_index": 0, "multiple_defensible": false, "none_defensible": false}'
    assert _verify(resp, _mcq(correct_index=0)) is None


def test_disagreement_is_wrong_answer_key() -> None:
    resp = '{"answer_index": 2, "multiple_defensible": false, "none_defensible": false}'
    flaw = _verify(resp, _mcq(correct_index=0))
    assert flaw is not None and flaw["code"] == "wrong_answer_key"


def test_multiple_defensible_is_more_than_one_correct() -> None:
    resp = '{"answer_index": 0, "multiple_defensible": true, "none_defensible": false}'
    flaw = _verify(resp, _mcq(correct_index=0))
    assert flaw is not None and flaw["code"] == "more_than_one_correct"


def test_none_defensible_is_not_grounded() -> None:
    resp = '{"answer_index": 0, "multiple_defensible": false, "none_defensible": true}'
    flaw = _verify(resp, _mcq(correct_index=0))
    assert flaw is not None and flaw["code"] == "not_grounded"


def test_unparseable_verifier_response_does_not_reject() -> None:
    # Conservative: a broken verifier reply must not punish an otherwise-good item.
    assert _verify("the model rambled, no json here", _mcq(correct_index=0)) is None


def test_out_of_range_index_does_not_reject() -> None:
    # A nonsense index (>= number of options) is a malformed reply, not a real
    # disagreement — must not reject a good item.
    resp = '{"answer_index": 9, "multiple_defensible": false, "none_defensible": false}'
    assert _verify(resp, _mcq(correct_index=0)) is None


def test_structural_problem_is_left_to_the_heuristic_gate() -> None:
    # Out-of-range key: verifier abstains (returns None) without an LLM call.
    with patch("app.services.mcq_quality._complete_chat_sync") as llm:
        out = verify_answer_key(
            MagicMock(),
            mcq={"question": "Q?", "options": ["a", "b"], "correct_index": 9},
            page_text="src",
        )
    assert out is None
    llm.assert_not_called()


def test_verify_answer_key_on_by_default() -> None:
    from app.config import Settings

    assert Settings().verify_answer_key is True
