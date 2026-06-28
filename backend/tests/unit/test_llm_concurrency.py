"""Tests for process-wide LLM concurrency cap."""

from __future__ import annotations

import asyncio
import threading

from app.eta.llm_concurrency import LLM_MAX_CONCURRENT, llm_slot_async, llm_slot_sync


def test_thread_semaphore_limits_parallel_sync_slots() -> None:
    active = 0
    peak = 0
    lock = threading.Lock()
    barrier = threading.Barrier(min(4, LLM_MAX_CONCURRENT))

    def worker() -> None:
        nonlocal active, peak
        with llm_slot_sync():
            with lock:
                active += 1
                peak = max(peak, active)
            barrier.wait(timeout=2)
            with lock:
                active -= 1

    threads = [threading.Thread(target=worker) for _ in range(min(4, LLM_MAX_CONCURRENT))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert peak <= LLM_MAX_CONCURRENT


def test_async_slot_uses_same_thread_semaphore() -> None:
    async def run() -> None:
        async with llm_slot_async():
            return True

    assert asyncio.run(run()) is True
