"""Unit tests for debug curation and grading."""

from __future__ import annotations

import pytest

from app.services.debug_curation import build_full_payload, public_payload
from app.services.debug_grading import grade_step


def test_build_full_payload_requires_steps():
    with pytest.raises(ValueError, match="At least one"):
        build_full_payload(
            title="T",
            scenario_type="code_reading",
            case={"summary": "x"},
            steps=[],
        )


def test_public_payload_strips_answers():
    payload = build_full_payload(
        title="Bug",
        scenario_type="code_reading",
        case={"summary": "Fails"},
        steps=[
            {
                "key": "root_cause",
                "question": "What?",
                "options": ["a", "b"],
                "correct_index": 1,
                "explanation": "secret",
            }
        ],
    )
    pub = public_payload(payload)
    assert "correct_index" not in (pub.get("steps") or [{}])[0]
    assert "explanation" not in (pub.get("steps") or [{}])[0]
    assert "_cook_qa" not in pub


def test_grade_step():
    payload = build_full_payload(
        title="Bug",
        scenario_type="code_reading",
        case={"summary": "Fails"},
        steps=[
            {
                "key": "root_cause",
                "question": "What?",
                "options": ["a", "b"],
                "correct_index": 1,
                "explanation": "because b",
            }
        ],
    )
    wrong = grade_step(payload, "root_cause", 0)
    assert wrong["correct"] is False
    assert wrong["correct_index"] == 1
    right = grade_step(payload, "root_cause", 1)
    assert right["correct"] is True