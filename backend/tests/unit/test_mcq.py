"""MCQ schema and LangGraph grading."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graphs.mcq_graph import _check_answer, grade_mcq_answer, try_grade_mcq_fast
from app.schemas.mcq import McqQuestion


def test_mcq_question_validates_options() -> None:
    q = McqQuestion(
        question="What is Kitab-ul-Hind?",
        options=["A book", "A city", "A ruler"],
        correct_index=0,
        explanation="It is Al-Biruni's work.",
    )
    assert q.correct_index == 0


def test_mcq_question_rejects_bad_index() -> None:
    with pytest.raises(ValueError):
        McqQuestion(
            question="Sample?",
            options=["A", "B"],
            correct_index=3,
        )


def test_check_answer_correct() -> None:
    out = _check_answer(
        {
            "question": "Q?",
            "options": ["A", "B"],
            "correct_index": 1,
            "selected_index": 1,
            "explanation": "Because B.",
        }
    )
    assert out["is_correct"] is True
    assert out["feedback_ready"] is True
    assert "Because B" in out["feedback"]


def test_try_grade_mcq_fast_correct() -> None:
    out = try_grade_mcq_fast(
        options=["A", "B"],
        correct_index=1,
        selected_index=1,
        explanation="Because B.",
    )
    assert out is not None
    assert out["is_correct"] is True
    assert "Because B" in out["feedback"]


def test_try_grade_mcq_fast_wrong_with_explanation() -> None:
    out = try_grade_mcq_fast(
        options=["A", "B"],
        correct_index=1,
        selected_index=0,
        explanation="B fits because it matches how European accounts spread through print.",
    )
    assert out is not None
    assert out["is_correct"] is False
    assert "B fits" in out["feedback"]
    assert "You chose A" in out["feedback"]


def test_try_grade_mcq_fast_wrong_without_explanation() -> None:
    out = try_grade_mcq_fast(
        options=["A", "B"],
        correct_index=1,
        selected_index=0,
        explanation="",
    )
    assert out is None


def test_grade_mcq_answer_correct_skips_llm() -> None:
    db = MagicMock()

    async def run() -> None:
        with patch("app.graphs.mcq_graph.complete_chat", new_callable=AsyncMock) as mock_complete:
            result = await grade_mcq_answer(
                db,
                question="Q?",
                options=["A", "B"],
                correct_index=1,
                selected_index=1,
                explanation="Because B.",
                document_context="",
            )
        assert result["is_correct"] is True
        mock_complete.assert_not_called()

    asyncio.run(run())


def test_grade_mcq_answer_wrong_uses_llm() -> None:
    db = MagicMock()

    async def run() -> None:
        with patch("app.graphs.mcq_graph.complete_chat", new_callable=AsyncMock) as mock_complete:
            mock_complete.return_value = "The correct answer is B because…"
            result = await grade_mcq_answer(
                db,
                question="Q?",
                options=["A", "B"],
                correct_index=1,
                selected_index=0,
                explanation="",
                document_context="",
            )
        assert result["is_correct"] is False
        assert "correct answer" in result["feedback"].lower()
        mock_complete.assert_called_once()

    asyncio.run(run())
