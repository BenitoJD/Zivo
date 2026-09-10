"""Per-provider concurrency cap (qb.llm_providers.max_concurrency).

Step Fun rejects the 9th concurrent call outright, so the limit has to hold the
9th caller instead of letting it out to earn a 429.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app.eta.llm_concurrency import _slot_key, provider_slot_async, provider_slot_sync


@pytest.fixture(autouse=True)
def _fake_pg_slots():
    """Stand in for Postgres advisory locks: every slot is free."""
    conn = MagicMock()
    conn.execute.return_value.scalar.return_value = True
    engine = MagicMock()
    engine.connect.return_value = conn
    with patch.dict("sys.modules", {"app.db": MagicMock(engine=engine)}):
        yield engine


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


def test_async_acquire_survives_a_tiny_executor_pool() -> None:
    """Blocked acquires must never eat the worker pool (2-core pod deadlock).

    With a 2-worker pool and a cap of 2, a blocking to_thread(acquire) wedged
    forever on the CI runner: every worker parked inside acquire, so the
    holders could not run their releases. The 10s ceiling turns a regression
    into a failure instead of a hang.
    """
    import concurrent.futures

    async def main() -> None:
        state = {"live": 0, "peak": 0}

        async def hold() -> None:
            async with provider_slot_async("prov-tiny", 2):
                state["live"] += 1
                state["peak"] = max(state["peak"], state["live"])
                await asyncio.sleep(0.01)
                state["live"] -= 1

        async def capped() -> None:
            loop = asyncio.get_running_loop()
            loop.set_default_executor(
                concurrent.futures.ThreadPoolExecutor(max_workers=2)
            )
            await asyncio.gather(*(hold() for _ in range(6)))

        await asyncio.wait_for(capped(), timeout=10)

    asyncio.run(main())


def test_slot_key_is_stable_and_int4() -> None:
    assert _slot_key("stepfun") == _slot_key("stepfun")
    assert _slot_key("stepfun") != _slot_key("deepseek")
    # Must fit Postgres int4 or pg_try_advisory_lock(int4, int4) errors out.
    assert -(2**31) <= _slot_key("stepfun") < 2**31


def test_global_slot_is_released_after_the_call(_fake_pg_slots) -> None:
    with provider_slot_sync("stepfun", 8):
        pass
    # Closing the connection is what drops the advisory lock — never leak it.
    _fake_pg_slots.connect.return_value.close.assert_called()


def test_fails_open_when_the_database_is_unreachable() -> None:
    engine = MagicMock()
    engine.connect.side_effect = RuntimeError("pool exhausted")
    with patch.dict("sys.modules", {"app.db": MagicMock(engine=engine)}):
        ran = False
        with provider_slot_sync("stepfun", 8):
            ran = True
        # A limiter outage must never wedge generation.
        assert ran


def test_limit_change_rebuilds_the_semaphore() -> None:
    with provider_slot_sync("prov-resize", 1):
        pass
    # An admin lowering/raising the registry value must take effect without a
    # restart; the old semaphore object is simply replaced.
    with provider_slot_sync("prov-resize", 4):
        with provider_slot_sync("prov-resize", 4):
            pass
