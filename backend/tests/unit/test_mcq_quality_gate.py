"""Quality gate — Gate A (force thinking, not recognition) is enforced as fatal."""

from __future__ import annotations

from app.services.mcq_quality import FATAL_FLAW_CODES, _critique_passes
from app.services.prompts import DEFAULTS


def test_recognition_only_is_a_fatal_flaw() -> None:
    assert "recognition_only" in FATAL_FLAW_CODES


def test_keyword_spotting_question_fails_even_if_critic_marks_pass() -> None:
    # A critic that says pass=True but reports a recognition_only fatal flaw must
    # still be rejected — keyword-spotting is a flaw, not a pass.
    critique = {"pass": True, "flaw_count": 0, "fatal_flaws": ["recognition_only"]}
    assert _critique_passes(critique) is False


def test_clean_thinking_question_still_passes() -> None:
    critique = {"pass": True, "flaw_count": 0, "fatal_flaws": []}
    assert _critique_passes(critique) is True


def test_critic_prompt_defines_the_recognition_axis() -> None:
    system = DEFAULTS["mcq_critic_system"]
    assert "recognition_only" in system
    # The framing the slug depends on must be present, not just the slug.
    assert "force THINKING" in system or "forces thinking" in system.lower()
