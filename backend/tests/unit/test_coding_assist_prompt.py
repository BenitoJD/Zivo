"""coding_assist prompt helpers (no DB)."""

from __future__ import annotations

from app.services.coding_assist import _editor_context_block, _problem_context_block


def test_problem_context_includes_title_and_samples() -> None:
    block = _problem_context_block(
        {
            "title": "FizzBuzz",
            "difficulty": "easy",
            "tags": ["loops"],
            "statement": "Print fizz",
            "sample_tests": [{"stdin": "1", "expected_output": "1"}],
        }
    )
    assert "FizzBuzz" in block
    assert "Print fizz" in block
    assert "Sample 1" in block


def test_editor_context_clips_code() -> None:
    block = _editor_context_block(
        code="x" * 20000,
        language_id=71,
        stdin="1",
        last_status="Accepted",
    )
    assert "Python" in block
    assert "Accepted" in block
    assert len(block) < 20000
