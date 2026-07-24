"""Unit tests for prompt cache breakpoints."""

from app.services.llm_prompt_cache import apply_prompt_cache
from app.services.mcq_dedup import SUBJECT_MATTER_PREFIX


def test_apply_prompt_cache_noop_for_non_anthropic() -> None:
    messages = [{"role": "system", "content": "hello"}]
    assert apply_prompt_cache(messages, provider_slug="openai") is messages


def test_apply_prompt_cache_marks_anthropic_system_and_page_prefix() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": f"{SUBJECT_MATTER_PREFIX}\nbody"},
        {"role": "user", "content": "variable"},
    ]
    out = apply_prompt_cache(messages, provider_slug="anthropic")
    assert out[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert out[1]["content"][0]["text"].startswith(SUBJECT_MATTER_PREFIX)
    assert out[2]["content"] == "variable"


def test_apply_prompt_cache_marks_page_text_and_grounding() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Page text:\nexcerpt"},
        {"role": "user", "content": "Grounding context (facts):\nctx"},
        {"role": "user", "content": "variable ask"},
    ]
    out = apply_prompt_cache(messages, provider_slug="anthropic")
    assert out[1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert out[2]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert out[3]["content"] == "variable ask"
