"""LLM Prose Engine — normalize generated text at the LLM output boundary.

Design: docs/LLM_PROSE_ENGINE.md
Version: qb.llm_prose.v1

Every completion routed through ``llm_router`` passes here so product copy never
ships with em dashes (or other AI-typical dash glyphs). Verbatim paths (e.g. OCR
of a learner's handwriting) opt out via ``verbatim=True``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.engine_runtime import Pred, Rule, apply, first_match

LLM_PROSE_VERSION = "qb.llm_prose.v1"
DEFAULT_POLICY = "no_em_dash_v1"

# Single-codepoint replacements: safe for per-token streaming in stream_chat_completion.
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


def _sanitize(text: str) -> LlmProseVerdict:
    mapped = [_DASH_TABLE.get(ord(ch), ch) for ch in text]
    replacements = sum(map(lambda ch: int(ord(ch) in _DASH_TABLE), text))
    return LlmProseVerdict(text="".join(mapped), replacements=replacements)


_PROSE_RULES = (
    Rule(when=(Pred("verbatim", "truthy"),), action="keep"),
    Rule(when=(Pred("empty", "truthy"),), action="keep"),
    Rule(when=(), action="sanitize"),
)


def sanitize_llm_output(text: str, *, verbatim: bool = False) -> LlmProseVerdict:
    """Normalize LLM-generated prose. Returns input unchanged when ``verbatim``."""
    return apply(
        first_match(
            _PROSE_RULES,
            {"verbatim": verbatim, "empty": not text},
        ).action,
        {
            "keep": lambda: LlmProseVerdict(text=text, replacements=0),
            "sanitize": lambda: _sanitize(text),
        },
    )
