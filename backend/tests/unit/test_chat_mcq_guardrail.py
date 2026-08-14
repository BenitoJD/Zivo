"""Tutor must not reveal MCQ answers unless the learner asks."""

from __future__ import annotations

from study_api.chat import (
    _is_prefetched_reference,
    _learn_context_has_active_question,
    _message_has_prefetched_reference,
    _user_requests_mcq_answer,
)
from app.services.prompts import DEFAULTS


def test_tutor_system_prompt_forbids_unsolicited_mcq_answer() -> None:
    prompt = DEFAULTS["tutor_system"]
    assert "Active quiz" in prompt
    assert "correct answer is" in prompt.lower()
    assert "unless the learner explicitly asks" in prompt.lower()


def test_tutor_system_prompt_has_warm_concise_voice_directives() -> None:
    """Lock in the warm, plain, concise voice so the rewrite is not eroded."""
    prompt = DEFAULTS["tutor_system"].lower()
    # Answers the actual question first, in plain words.
    assert "answer" in prompt and "first" in prompt
    assert "plain" in prompt
    assert "wikipedia summary" in prompt
    # Explicitly forbids robotic document-anchored openers (the directive
    # matters, not merely that the phrase is absent — the prompt names the
    # forbidden phrases to instruct against them).
    assert "never open with" in prompt


def test_user_requests_mcq_answer_detects_explicit_asks() -> None:
    assert _user_requests_mcq_answer("What's the correct answer?")
    assert _user_requests_mcq_answer("Which option is right?")
    assert _user_requests_mcq_answer("What should I pick?")


def test_user_requests_mcq_answer_false_for_concept_questions() -> None:
    assert not _user_requests_mcq_answer(
        "What is the difference between app router and page router?"
    )
    assert not _user_requests_mcq_answer("Explain server components simply")
    assert not _user_requests_mcq_answer("Give me a hint without the answer")


def test_learn_context_has_active_question() -> None:
    block = 'Current question stem: "Which is true?"\n  A. One'
    assert _learn_context_has_active_question(block)
    assert not _learn_context_has_active_question("Document study complete")


def test_prefetched_reference_scope_and_message() -> None:
    assert _is_prefetched_reference({"reference_source": "wikipedia"})
    assert not _is_prefetched_reference({"reference_source": "other"})
    msg = 'Wikipedia summary of "Ombudsman":\nAn ombudsman is...'
    assert _message_has_prefetched_reference(msg)
    assert not _message_has_prefetched_reference("Explain hashing")
