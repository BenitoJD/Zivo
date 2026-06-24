"""Token budget helpers — consistent sizing across char-based caps."""

from __future__ import annotations

import functools
import os

_CHARS_PER_TOKEN = float(os.getenv("ZIVO_CHARS_PER_TOKEN", "3.5"))


@functools.lru_cache(maxsize=1)
def _tiktoken_encoding():
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def count_tokens(text: str, *, model: str = "") -> int:
    del model  # reserved for per-model encodings later
    enc = _tiktoken_encoding()
    if enc is not None:
        return len(enc.encode(text or ""))
    return max(1, int(len(text or "") / _CHARS_PER_TOKEN))


def truncate_to_tokens(text: str, max_tokens: int, *, model: str = "") -> str:
    del model
    if not text or max_tokens <= 0:
        return ""
    enc = _tiktoken_encoding()
    if enc is not None:
        tokens = enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        return enc.decode(tokens[:max_tokens])
    return text[: int(max_tokens * _CHARS_PER_TOKEN)]


def token_budget_chars(max_tokens: int) -> int:
    """Approximate char budget for a token limit."""
    return int(max_tokens * _CHARS_PER_TOKEN)
