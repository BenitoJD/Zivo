"""MCQ schema validation guards."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.schemas.mcq import McqGradeRequest


def _base_request() -> dict[str, object]:
    return {
        "document_id": str(uuid.uuid4()),
        "question": "What is 2+2?",
        "options": ["3", "4", "5"],
        "correct_index": 1,
        "selected_index": 0,
        "explanation": "Two plus two equals four.",
    }


class TestMcqGradeRequestValidation:
    def test_accepts_well_formed_request(self) -> None:
        req = McqGradeRequest(**_base_request())  # type: ignore[arg-type]
        assert req.question == "What is 2+2?"

    def test_rejects_short_question(self) -> None:
        data = _base_request()
        data["question"] = "abc"
        with pytest.raises(ValidationError):
            McqGradeRequest(**data)  # type: ignore[arg-type]

    def test_rejects_overlong_question(self) -> None:
        data = _base_request()
        data["question"] = "x" * 2001
        with pytest.raises(ValidationError):
            McqGradeRequest(**data)  # type: ignore[arg-type]

    def test_rejects_overlong_explanation(self) -> None:
        data = _base_request()
        data["explanation"] = "x" * 4001
        with pytest.raises(ValidationError):
            McqGradeRequest(**data)  # type: ignore[arg-type]

    def test_rejects_too_few_options(self) -> None:
        data = _base_request()
        data["options"] = ["only"]
        with pytest.raises(ValidationError):
            McqGradeRequest(**data)  # type: ignore[arg-type]

    def test_rejects_too_many_options(self) -> None:
        data = _base_request()
        data["options"] = [f"opt{i}" for i in range(11)]
        with pytest.raises(ValidationError):
            McqGradeRequest(**data)  # type: ignore[arg-type]

    def test_rejects_negative_index(self) -> None:
        data = _base_request()
        data["selected_index"] = -1
        with pytest.raises(ValidationError):
            McqGradeRequest(**data)  # type: ignore[arg-type]
