"""Process-wide cap on concurrent LLM calls (async API, sync CPU workers, threads)."""

from __future__ import annotations

import asyncio
import os
import threading
from contextlib import asynccontextmanager, contextmanager

LLM_MAX_CONCURRENT = int(os.getenv("LLM_MAX_CONCURRENT", "8"))

_thread_semaphore = threading.Semaphore(LLM_MAX_CONCURRENT)


@contextmanager
def llm_slot_sync():
    """Cross-thread slot for sync worker paths (threading.Semaphore)."""
    _thread_semaphore.acquire()
    try:
        yield
    finally:
        _thread_semaphore.release()


@asynccontextmanager
async def llm_slot_async():
    """Same process-wide cap for asyncio workers and the API."""
    await asyncio.to_thread(_thread_semaphore.acquire)
    try:
        yield
    finally:
        await asyncio.to_thread(_thread_semaphore.release)
