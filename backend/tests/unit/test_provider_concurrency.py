"""Per-provider concurrency cap (qb.llm_providers.max_concurrency).

Step Fun rejects the 9th concurrent call outright, so the limit has to hold the
9th caller instead of letting it out to earn a 429.
"""

from __future__ import annotations

import asyncio

from app.eta.llm_concurrency import provider_slot_async, provider_slot_sync


def test_no_limit_is_a_noop() -> None:
    with provider_slot_sync("stepfun", None):
        pass
    with provider_slot_sync(None, 8):
        pass
    with provider_slot_sync("stepfun", 0):
        pass


def test_sync_slot_caps_concurrent_holders() -> None:
    import threading

    limit, live, peak = 2, 0, 0
    lock = threading.Lock()
    release = threading.Event()

    def hold() -> None:
        nonlocal live, peak
        with provider_slot_sync("prov-sync", limit):
            with lock:
                live += 1
                peak = max(peak, live)
            release.wait(timeout=2)
            with lock:
                live -= 1

    threads = [threading.Thread(target=hold) for _ in range(5)]
    for t in threads:
        t.start()
    # Give the runnable ones time to pile up against the cap, then let go.
    threading.Event().wait(0.15)
    release.set()
    for t in threads:
        t.join(timeout=3)
    assert peak <= limit


def test_async_slot_caps_concurrent_holders() -> None:
    limit = 3
    state = {"live": 0, "peak": 0}

    async def hold() -> None:
        async with provider_slot_async("prov-async", limit):
            state["live"] += 1
            state["peak"] = max(state["peak"], state["live"])
            await asyncio.sleep(0.05)
            state["live"] -= 1

    async def main() -> None:
        await asyncio.gather(*(hold() for _ in range(9)))

    asyncio.run(main())
    assert state["peak"] <= limit
    assert state["live"] == 0


def test_limit_change_rebuilds_the_semaphore() -> None:
    with provider_slot_sync("prov-resize", 1):
        pass
    # An admin lowering/raising the registry value must take effect without a
    # restart; the old semaphore object is simply replaced.
    with provider_slot_sync("prov-resize", 4):
        with provider_slot_sync("prov-resize", 4):
            pass
