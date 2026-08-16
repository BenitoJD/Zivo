"""Run async LLM calls from CPU worker threads with a reused event loop."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import TypeVar

from app.engine_runtime import pick
from app.eta.llm_concurrency import llm_slot_sync

T = TypeVar("T")

_thread_local = threading.local()


def _new_worker_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _thread_local.loop = loop
    return loop


def get_worker_event_loop() -> asyncio.AbstractEventLoop:
    loop = getattr(_thread_local, "loop", None)
    return pick(loop is None or loop.is_closed(), _new_worker_loop, lambda: loop)


def run_coro_in_worker(coro: Coroutine[object, object, T]) -> T:
    """Acquire an LLM slot and run *coro* on this thread's persistent loop."""
    with llm_slot_sync():
        return get_worker_event_loop().run_until_complete(coro)
