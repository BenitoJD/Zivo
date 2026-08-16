"""Cluster-safe rate limiter — bucket counting, 429 path, fail-open, sweep.

The DB-backed counter lives in ``qb.rate_limit_hit``; these tests inject a mock
session so they run without a database. The cluster-shared behavior across
"pods" (independent calls sharing a session) is covered by the DB-gated
integration test ``test_rate_limit_db.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from app.engine_runtime import pick
from app.services import rate_limit


def _req(peer: str = "203.0.113.9") -> MagicMock:
    """Minimal Request double: client_ip() reads .client.host."""
    return SimpleNamespace(client=SimpleNamespace(host=peer))


def _session_with_counter(*, start: int = 0) -> MagicMock:
    """A mock Session whose INSERT upsert returns an incrementing hit_count.

    Only the rate-limit INSERT (containing 'RETURNING') advances the counter;
    the sweep DELETE is a no-op so it doesn't inflate the count.
    """
    state = {"count": start}

    class _Result:
        def __init__(self, value: int) -> None:
            self._value = value

        def scalar(self) -> int:
            return self._value

    def execute(stmt, params=None):
        sql = str(stmt)
        return pick(
            "RETURNING" in sql,
            lambda: (state.__setitem__("count", state["count"] + 1) or _Result(state["count"])),
            lambda: MagicMock(),
        )

    sess = MagicMock()
    sess.execute.side_effect = execute
    sess._state = state
    return sess


def _settings(limit: int) -> object:
    return SimpleNamespace(rate_limit_per_minute=limit)


def test_disabled_when_limit_zero() -> None:
    """rate_limit_per_minute <= 0 short-circuits before touching the DB."""
    with (
        patch.object(rate_limit, "get_settings", return_value=_settings(0)),
        patch.object(rate_limit, "client_ip", side_effect=AssertionError("should not read IP")),
    ):
        # Must not raise and must not touch the session.
        rate_limit.rate_limit(_req(), db=MagicMock())


def test_allows_up_to_limit_then_429() -> None:
    """limit=3 allows 3 hits; the 4th trips 429 (count > limit)."""
    with patch.object(rate_limit, "get_settings", return_value=_settings(3)):
        sess = _session_with_counter()
        rate_limit.rate_limit(_req(), db=sess)  # count=1, allowed
        rate_limit.rate_limit(_req(), db=sess)  # count=2, allowed
        rate_limit.rate_limit(_req(), db=sess)  # count=3, allowed (not > 3)
        with pytest.raises(HTTPException) as exc:
            rate_limit.rate_limit(_req(), db=sess)  # count=4 -> 429
        assert exc.value.status_code == 429
        assert sess._state["count"] == 4  # counter advanced on the over-limit hit


def test_counter_keys_by_client_ip() -> None:
    """Different client IPs get independent counters (mock returns per-key)."""
    counts: dict[str, int] = {}

    class _Result:
        def __init__(self, value: int) -> None:
            self._value = value

        def scalar(self) -> int:
            return self._value

    def execute(stmt, params=None):
        sql = str(stmt)
        return pick(
            "RETURNING" not in sql,
            lambda: MagicMock(),
            lambda: (
                counts.__setitem__(params["key"], counts.get(params["key"], 0) + 1)
                or _Result(counts[params["key"]])
            ),
        )

    sess = MagicMock()
    sess.execute.side_effect = execute
    with patch.object(rate_limit, "get_settings", return_value=_settings(2)):
        # IP A hits twice (allowed), IP B hits twice (allowed)
        for _ in range(2):
            rate_limit.rate_limit(_req("10.0.0.1"), db=sess)
        for _ in range(2):
            rate_limit.rate_limit(_req("10.0.0.2"), db=sess)
        assert counts == {"10.0.0.1": 2, "10.0.0.2": 2}
        # A's 3rd hit trips 429 (3 > 2).
        with pytest.raises(HTTPException):
            rate_limit.rate_limit(_req("10.0.0.1"), db=sess)
        # B's 3rd hit also trips 429 (3 > 2).
        with pytest.raises(HTTPException):
            rate_limit.rate_limit(_req("10.0.0.2"), db=sess)
        assert counts == {"10.0.0.1": 3, "10.0.0.2": 3}


def test_db_failure_fails_open() -> None:
    """A DB error must NOT block the request — limiter is availability, not auth."""
    sess = MagicMock()
    sess.execute.side_effect = OperationalError("stmt", params={}, orig=Exception("db down"))
    with patch.object(rate_limit, "get_settings", return_value=_settings(5)):
        # Must not raise.
        rate_limit.rate_limit(_req(), db=sess)
    sess.rollback.assert_called_once()


def test_sweep_is_throttled() -> None:
    """_maybe_sweep only runs once per _SWEEP_MIN_INTERVAL_S window."""
    # First call: last_sweep is at t=0, now=10 -> 10s < 300s, throttled.
    rate_limit._last_sweep_monotonic = 0.0
    sess = MagicMock()
    with patch.object(rate_limit.time, "monotonic", return_value=10.0):
        rate_limit._maybe_sweep(sess)
        assert sess.execute.call_count == 0  # throttled
    # Second call: now=400 -> 400-0=400 > 300, runs; updates last_sweep to 400.
    with patch.object(rate_limit.time, "monotonic", return_value=400.0):
        rate_limit._maybe_sweep(sess)
        assert sess.execute.call_count == 1
    # Third call: now=401 -> 401-400=1 < 300, throttled again.
    with patch.object(rate_limit.time, "monotonic", return_value=401.0):
        rate_limit._maybe_sweep(sess)
        assert sess.execute.call_count == 1
    rate_limit._last_sweep_monotonic = 0.0  # reset for other tests


def test_dependency_wrapper_is_passthrough() -> None:
    assert rate_limit.rate_limit_dependency(None) is None
