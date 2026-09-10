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

from app.engine_runtime import Pred, Rule, apply, first_match, pick

logger = logging.getLogger(__name__)

LLM_MAX_CONCURRENT = int(os.getenv("LLM_MAX_CONCURRENT", "8"))

_thread_semaphore = threading.Semaphore(LLM_MAX_CONCURRENT)

_SLOT_POLL_SECONDS = 0.05


async def _acquire_async(sem: threading.Semaphore) -> None:
    """Claim a semaphore without parking a pool thread on a blocked acquire.

    asyncio.to_thread(sem.acquire) strands one default-executor worker per
    waiter; that pool is cpu_count() + 4, so on a small pod the waiters alone
    can consume every worker and the holders' releases never run: deadlock.
    Polling keeps each worker visit microscopic and the loop responsive.
    """
    while not await asyncio.to_thread(sem.acquire, blocking=False):
        await asyncio.sleep(_SLOT_POLL_SECONDS)


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
    await _acquire_async(_thread_semaphore)
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
# own, so 7 pods x 8 still shows Step Fun 56 concurrent calls (measured: the
# 429s continued after the per-process cap shipped). The cluster-wide gate is
# _global_slot(), which claims one of `limit` Postgres advisory-lock slots, so
# the ceiling holds across every pod.
_provider_semaphores: dict[str, threading.Semaphore] = {}
_provider_limits: dict[str, int] = {}
_provider_lock = threading.Lock()


def _provider_semaphore(slug: str, limit: int) -> threading.Semaphore:
    with _provider_lock:
        def _rebuild() -> None:
            _provider_limits[slug] = limit
            _provider_semaphores[slug] = threading.Semaphore(limit)

        pick(_provider_limits.get(slug) != limit, _rebuild, lambda: None)
        return _provider_semaphores[slug]


def _slot_key(slug: str) -> int:
    """Stable int4 advisory-lock namespace for a provider slug."""
    digest = hashlib.sha256(slug.encode()).digest()
    return int.from_bytes(digest[:4], "big", signed=True)


# How long to keep trying for a free slot before giving up and calling anyway.
# Fail-OPEN on purpose: a coordination hiccup must never wedge generation. The
# provider's own 429 is a survivable outcome, a permanently blocked worker isn't.
GLOBAL_SLOT_WAIT_SECONDS = float(os.getenv("LLM_GLOBAL_SLOT_WAIT", "45"))
_GLOBAL_SLOT_POLL_SECONDS = 0.25

_SLOT_ROUND_RULES = (
    Rule(when=(Pred("acquired", "truthy"),), action="hold"),
    Rule(when=(Pred("expired", "truthy"),), action="open"),
    Rule(when=(), action="retry"),
)


def _try_slot(conn, key: int, slot: int) -> int | None:
    got = conn.execute(
        text("SELECT pg_try_advisory_lock(:k, :s)"), {"k": key, "s": slot}
    ).scalar()
    return pick(bool(got), lambda: slot, lambda: None)


def _lock_one(conn, key: int, limit: int) -> int | None:
    return next(
        filter(
            lambda held: held is not None,
            map(lambda slot: _try_slot(conn, key, slot), range(limit)),
        ),
        None,
    )


@contextmanager
def _global_slot(slug: str, limit: int):
    """Hold one of ``limit`` cluster-wide slots for a provider.

    Postgres advisory locks are SESSION-scoped, so a crashed pod releases its
    slot automatically: no lease table, no stale-slot reaper. Waiters poll on a
    connection they close between attempts, so only actual holders (<= limit
    cluster-wide) tie up a connection.
    """
    from app.db import engine

    key = _slot_key(slug)
    deadline = time.monotonic() + GLOBAL_SLOT_WAIT_SECONDS
    conn = None
    acquired = False
    outcome = "retry"

    def _close_conn() -> None:
        nonlocal conn
        holder = conn
        conn = None
        pick(holder is None, lambda: None, lambda: holder.close())

    def _try_round() -> None:
        nonlocal conn, acquired
        conn = engine.connect()
        acquired = _lock_one(conn, key, limit) is not None
        pick(acquired, lambda: None, _close_conn)

    try:
        while outcome == "retry":
            try:
                _try_round()
            except Exception:
                logger.debug("global provider slot unavailable for %s", slug, exc_info=True)
                _close_conn()
                yield
                return
            hit = first_match(
                _SLOT_ROUND_RULES,
                {
                    "acquired": acquired,
                    "expired": time.monotonic() >= deadline,
                },
            )
            outcome = hit.action
            apply(
                outcome,
                {
                    "hold": lambda: None,
                    "open": lambda: logger.warning(
                        "provider %s: no free slot after %ss (limit=%s) — proceeding uncapped",
                        slug,
                        GLOBAL_SLOT_WAIT_SECONDS,
                        limit,
                    ),
                    "retry": lambda: time.sleep(_GLOBAL_SLOT_POLL_SECONDS),
                },
            )
        yield
    finally:
        _close_conn()


def _skip_provider(slug: str | None, limit: int | None) -> bool:
    return (not slug) or (not limit) or (limit <= 0)


@contextmanager
def provider_slot_sync(slug: str | None, limit: int | None):
    """Cap concurrent calls to one provider, per-process AND cluster-wide.
    No limit -> no-op.
    """

    def _noop():
        yield

    def _gated():
        sem = _provider_semaphore(slug, int(limit))
        sem.acquire()
        try:
            with _global_slot(slug, int(limit)):
                yield
        finally:
            sem.release()

    yield from pick(_skip_provider(slug, limit), _noop, _gated)


async def _async_noop() -> None:
    return None


@asynccontextmanager
async def provider_slot_async(slug: str | None, limit: int | None):
    """Async counterpart of :func:`provider_slot_sync`.

    The blocking waits (semaphore + advisory-lock poll) run on worker threads so
    the event loop keeps serving while this call queues for a slot.
    """
    skip = _skip_provider(slug, limit)
    sem = pick(skip, lambda: None, lambda: _provider_semaphore(slug, int(limit)))
    slot = pick(skip, lambda: None, lambda: _global_slot(slug, int(limit)))
    await pick(skip, _async_noop, lambda: _acquire_async(sem))
    try:
        await pick(skip, _async_noop, lambda: asyncio.to_thread(slot.__enter__))
        try:
            yield
        finally:
            await pick(
                skip,
                _async_noop,
                lambda: asyncio.to_thread(slot.__exit__, None, None, None),
            )
    finally:
        await pick(skip, _async_noop, lambda: asyncio.to_thread(sem.release))
