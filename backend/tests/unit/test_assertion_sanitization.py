"""Assertion payload sanitization — answer key must not leak pre-grade.

Regression for the bug where /api/assertions/{id} returned correct_index,
explanation, and option_feedback in the payload, letting a learner with
devtools open see the answer before grading. The grade endpoint
(/api/mcq/grade) is the only legitimate source of the verdict + feedback.
"""

from __future__ import annotations

from app.api.assertions import _sanitize_assertion_payload


def test_strips_single_answer_key() -> None:
    payload = {
        "format": "qb.mcq.v1",
        "question": "What is 2+2?",
        "options": ["3", "4", "5"],
        "correct_index": 1,
        "explanation": "2+2 equals 4.",
        "option_feedback": {"0": "no", "1": "yes"},
        "quality": {"pass": True, "attempts": 1},
        "primary_concept": "arithmetic",
    }
    out = _sanitize_assertion_payload(payload)
    assert out["question"] == "What is 2+2?"
    assert out["options"] == ["3", "4", "5"]
    assert out["primary_concept"] == "arithmetic"
    # Answer key + per-option feedback + internal QA metadata must be gone.
    for leaked in ("correct_index", "correct_indices", "explanation", "option_feedback", "quality"):
        assert leaked not in out, f"{leaked} leaked into sanitized payload"
    # Single-answer item is not multi-select.
    assert out.get("is_multi") is False


def test_strips_multi_answer_key_but_keeps_is_multi_flag() -> None:
    payload = {
        "format": "qb.mcq.v1",
        "question": "Select all primes.",
        "options": ["2", "3", "4", "5"],
        "correct_indices": [0, 1, 3],
        "explanation": "2, 3, and 5 are prime.",
    }
    out = _sanitize_assertion_payload(payload)
    assert out.get("is_multi") is True
    assert "correct_indices" not in out
    assert "explanation" not in out


def test_handles_missing_or_non_dict_payload() -> None:
    assert _sanitize_assertion_payload(None) == {}
    assert _sanitize_assertion_payload("not a dict") == {}  # type: ignore[arg-type]


def test_preserves_question_and_options_when_only_those_present() -> None:
    payload = {"question": "Stem?", "options": ["A", "B"]}
    out = _sanitize_assertion_payload(payload)
    assert out == {"question": "Stem?", "options": ["A", "B"], "is_multi": False}
