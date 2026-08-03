"""LLM Prose Engine — normalize generated text at the LLM output boundary.

Design: docs/LLM_PROSE_ENGINE.md
Version: qb.llm_prose.v1

Every completion routed through ``llm_router`` passes here so product copy never
ships with em dashes (or other AI-typical dash glyphs). Verbatim paths (e.g. OCR
of a learner's handwriting) opt out via ``verbatim=True``.
"""

from __future__ import annotations

from dataclasses import dataclass

LLM_PROSE_VERSION = "qb.llm_prose.v1"
DEFAULT_POLICY = "no_em_dash_v1"

# Single-codepoint replacements — safe for per-token streaming in stream_chat_completion.
_DASH_TABLE = {
    0x2014: "-",  # em dash
    0x2013: "-",  # en dash
    0x2015: "-",  # horizontal bar
    0x2012: "-",  # figure dash
}


@dataclass(frozen=True)
class LlmProseVerdict:
    text: str
    replacements: int
    policy: str = DEFAULT_POLICY
    policy_version: str = LLM_PROSE_VERSION


def sanitize_llm_output(text: str, *, verbatim: bool = False) -> LlmProseVerdict:
    """Normalize LLM-generated prose. Returns input unchanged when ``verbatim``."""
    if verbatim or not text:
        return LlmProseVerdict(text=text, replacements=0)

    replacements = 0
    out: list[str] = []
    for ch in text:
        repl = _DASH_TABLE.get(ord(ch))
        if repl is not None:
            replacements += 1
            out.append(repl)
        else:
            out.append(ch)

    return LlmProseVerdict(
        text="".join(out),
        replacements=replacements,
    )

