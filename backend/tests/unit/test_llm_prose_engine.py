"""Unit tests for LLM Prose Engine."""

from app.services.llm_prose_engine import (
    LLM_PROSE_VERSION,
    sanitize_llm_output,
)


def test_sanitize_replaces_em_and_en_dash() -> None:
    verdict = sanitize_llm_output("Upload source — then generate")
    assert verdict.text == "Upload source - then generate"
    assert verdict.replacements == 1
    assert verdict.policy_version == LLM_PROSE_VERSION


def test_sanitize_multiple_dash_types() -> None:
    verdict = sanitize_llm_output("a—b–c―d")
    assert verdict.text == "a-b-c-d"
    assert verdict.replacements == 3


def test_sanitize_verbatim_skips() -> None:
    raw = "learner wrote — here"
    verdict = sanitize_llm_output(raw, verbatim=True)
    assert verdict.text == raw
    assert verdict.replacements == 0


def test_sanitize_empty() -> None:
    verdict = sanitize_llm_output("")
    assert verdict.text == ""
    assert verdict.replacements == 0

