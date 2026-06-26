"""Token budget helpers — consistent sizing across char-based caps."""

from __future__ import annotations

import functools
import os

_CHARS_PER_TOKEN = float(os.getenv("ZIVO_CHARS_PER_TOKEN", "3.5"))

# Completion caps — generous floors so reasoning models finish with parseable content.
OUTPUT_MAX_TOKENS_DEFAULT = int(os.getenv("ZIVO_LLM_OUTPUT_MAX_TOKENS", "8192"))
OUTPUT_MAX_TOKENS_BATCH = int(os.getenv("ZIVO_LLM_OUTPUT_MAX_TOKENS_BATCH", "16384"))
CHAT_OUTPUT_MAX_TOKENS = int(os.getenv("ZIVO_CHAT_MAX_TOKENS", "2048"))

# Input context sent to models (page text, RAG excerpts, chat blocks).
PAGE_INPUT_MAX_TOKENS = int(os.getenv("ZIVO_PAGE_INPUT_MAX_TOKENS", "32000"))
CHAT_INPUT_MAX_TOKENS = int(os.getenv("ZIVO_CHAT_INPUT_MAX_TOKENS", "16000"))
GRADE_CONTEXT_MAX_TOKENS = int(os.getenv("ZIVO_GRADE_CONTEXT_MAX_TOKENS", "8000"))
SUMMARIZE_CHUNK_INPUT_MAX_TOKENS = int(os.getenv("ZIVO_SUMMARIZE_CHUNK_INPUT_MAX_TOKENS", "8000"))
SUMMARIZE_ROLLUP_INPUT_MAX_TOKENS = int(os.getenv("ZIVO_SUMMARIZE_ROLLUP_INPUT_MAX_TOKENS", "32000"))
SUMMARIZE_SINGLE_SHOT_MAX_TOKENS = int(os.getenv("ZIVO_SUMMARIZE_SINGLE_SHOT_MAX_TOKENS", "100000"))

# Model meta below this is treated as stale and ignored at seed / apply time.
MIN_MODEL_META_MAX_TOKENS = int(os.getenv("ZIVO_MIN_MODEL_META_MAX_TOKENS", "8192"))


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
