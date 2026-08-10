"""Process-wide cap on concurrent LLM calls (async API, sync CPU workers, threads)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy import text

logger = logging.getLogger(__name__)

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
# The in-process semaphore below is only the first gate: N pods each get their
# own, so 7 pods x 8 still shows Step Fun 56 concurrent calls (measured — the
# 429s continued after the per-process cap shipped). The cluster-wide gate is
# _global_slot(), which claims one of `limit` Postgres advisory-lock slots, so
# the ceiling holds across every pod.
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


def _slot_key(slug: str) -> int:
    """Stable int4 advisory-lock namespace for a provider slug."""
    digest = hashlib.sha256(slug.encode()).digest()
    return int.from_bytes(digest[:4], "big", signed=True)


# How long to keep trying for a free slot before giving up and calling anyway.
# Fail-OPEN on purpose: a coordination hiccup must never wedge generation — the
# provider's own 429 is a survivable outcome, a permanently blocked worker isn't.
GLOBAL_SLOT_WAIT_SECONDS = float(os.getenv("LLM_GLOBAL_SLOT_WAIT", "45"))
_GLOBAL_SLOT_POLL_SECONDS = 0.25


@contextmanager
def _global_slot(slug: str, limit: int):
    """Hold one of ``limit`` cluster-wide slots for a provider.

    Postgres advisory locks are SESSION-scoped, so a crashed pod releases its
    slot automatically — no lease table, no stale-slot reaper. Waiters poll on a
    connection they close between attempts, so only actual holders (<= limit
    cluster-wide) tie up a connection.
    """
    from app.db import engine

    key = _slot_key(slug)
    deadline = time.monotonic() + GLOBAL_SLOT_WAIT_SECONDS
    conn = None
    try:
        while True:
            try:
                conn = engine.connect()
                for slot in range(limit):
                    got = conn.execute(
                        text("SELECT pg_try_advisory_lock(:k, :s)"), {"k": key, "s": slot}
                    ).scalar()
                    if got:
                        yield
                        return
                conn.close()
                conn = None
            except Exception:
                # DB unreachable / pool exhausted: don't block the LLM call on the
                # limiter. The in-process semaphore still applies.
                logger.debug("global provider slot unavailable for %s", slug, exc_info=True)
                if conn is not None:
                    conn.close()
                    conn = None
                yield
                return
            if time.monotonic() >= deadline:
                logger.warning(
                    "provider %s: no free slot after %ss (limit=%s) — proceeding uncapped",
                    slug,
                    GLOBAL_SLOT_WAIT_SECONDS,
                    limit,
                )
                yield
                return
            time.sleep(_GLOBAL_SLOT_POLL_SECONDS)
    finally:
        # Closing the session releases whichever advisory lock it held.
        if conn is not None:
            conn.close()


@contextmanager
def provider_slot_sync(slug: str | None, limit: int | None):
    """Cap concurrent calls to one provider, per-process AND cluster-wide.
    No limit -> no-op.
    """
    if not slug or not limit or limit <= 0:
        yield
        return
    sem = _provider_semaphore(slug, int(limit))
    sem.acquire()
    try:
        with _global_slot(slug, int(limit)):
            yield
    finally:
        sem.release()


@asynccontextmanager
async def provider_slot_async(slug: str | None, limit: int | None):
    """Async counterpart of :func:`provider_slot_sync`.

    The blocking waits (semaphore + advisory-lock poll) run on worker threads so
    the event loop keeps serving while this call queues for a slot.
    """
    if not slug or not limit or limit <= 0:
        yield
        return
    sem = _provider_semaphore(slug, int(limit))
    await asyncio.to_thread(sem.acquire)
    slot = _global_slot(slug, int(limit))
    try:
        await asyncio.to_thread(slot.__enter__)
        try:
            yield
        finally:
            await asyncio.to_thread(slot.__exit__, None, None, None)
    finally:
        await asyncio.to_thread(sem.release)
