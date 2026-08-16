"""Prompt templates render valid JSON examples for any model that copies them.

Regression: the generation system prompt double-braced its JSON example (to survive
str.format()), but the generation call sites use get_prompt WITHOUT format args, so the
doubles were never collapsed. A model that copies the example literally (GLM) emitted
`{{...}}`, which fails json.loads → zero questions generated. get_prompt now collapses
escaped braces when it isn't formatting.
"""

from __future__ import annotations

import json
import re

from app.engine_runtime import pick


class _StubQuery:
    def filter(self, *a, **k):  # noqa: D401
        return self

    def first(self):
        return None


class _StubDB:
    """Minimal stand-in: no SystemPrompt rows → get_prompt falls back to DEFAULTS."""

    def query(self, *a, **k):
        return _StubQuery()


def _example_block(text: str) -> str | None:
    m = re.search(r"```zv-mcq\s*(\{.*?\})\s*```", text, re.DOTALL)
    return pick(bool(m), lambda: m.group(1), lambda: None)


def test_generate_prompt_example_is_valid_single_brace_json() -> None:
    from app.services.prompts import get_prompt

    prompt = get_prompt(_StubDB(), "mcq_page_generate_system")
    assert "{{" not in prompt and "}}" not in prompt
    block = _example_block(prompt)
    assert block, "generation prompt must contain a zv-mcq JSON example"
    obj = json.loads(block)  # must be valid JSON, not double-braced
    assert "question" in obj and "options" in obj and "correct_index" in obj
