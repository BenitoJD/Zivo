"""Process-wide cap on concurrent LLM calls (async workers + API)."""

import asyncio
import os

LLM_MAX_CONCURRENT = int(os.getenv("LLM_MAX_CONCURRENT", "8"))

_llm_semaphore: asyncio.Semaphore | None = None


def get_llm_semaphore() -> asyncio.Semaphore:
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(LLM_MAX_CONCURRENT)
    return _llm_semaphore
