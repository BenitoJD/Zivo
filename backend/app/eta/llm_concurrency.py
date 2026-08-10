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


# --- Per-provider caps -------------------------------------------------------
# Providers differ: Step Fun rejects the 9th concurrent call outright, others are
# far more permissive. The limit is data (qb.llm_providers.max_concurrency), so a
# provider's ceiling is changed in the registry, not in a redeploy.
#
# ponytail: per-PROCESS, like the global cap above — N pods each get their own
# semaphore, so the true ceiling is N x limit. Sized right for one busy CPU
# worker; move to a Redis/DB token bucket only if multi-pod bursts prove to be
# what actually trips the provider.
_provider_semaphores: dict[str, threading.Semaphore] = {}
_provider_limits: dict[str, int] = {}
_provider_lock = threading.Lock()


def _provider_semaphore(slug: str, limit: int) -> threading.Semaphore:
    with _provider_lock:
        # Rebuild when the registry value changes, so an admin edit takes effect
        # without a restart (in-flight holders drain against the old object).
        if _provider_limits.get(slug) != limit:
            _provider_limits[slug] = limit
            _provider_semaphores[slug] = threading.Semaphore(limit)
        return _provider_semaphores[slug]


@contextmanager
def provider_slot_sync(slug: str | None, limit: int | None):
    """Cap concurrent calls to one provider. No limit -> no-op."""
    if not slug or not limit or limit <= 0:
        yield
        return
    sem = _provider_semaphore(slug, int(limit))
    sem.acquire()
    try:
        yield
    finally:
        sem.release()


@asynccontextmanager
async def provider_slot_async(slug: str | None, limit: int | None):
    """Async counterpart of :func:`provider_slot_sync`."""
    if not slug or not limit or limit <= 0:
        yield
        return
    sem = _provider_semaphore(slug, int(limit))
    await asyncio.to_thread(sem.acquire)
    try:
        yield
    finally:
        await asyncio.to_thread(sem.release)
