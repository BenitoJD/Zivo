"""DB-backed rate limiter — cluster-shared counting across "pods".

The unit test mocks the session; this one proves the real Postgres upsert
behaves as a shared counter: two independent sessions (simulating two API pods)
see the same hit_count and the (limit+1)th request trips 429 regardless of
which pod served it. This is the property the old in-memory limiter could not
provide.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.sql import text

from app.db import SessionLocal
from app.services import rate_limit


def _db_reachable() -> bool:
    try:
        db = SessionLocal()
        db.execute(select(1))
        db.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="dev DB not reachable")


def _req(peer: str) -> SimpleNamespace:
    return SimpleNamespace(client=SimpleNamespace(host=peer))


def _purge_table() -> None:
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM qb.rate_limit_hit"))
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clean_table():
    """Wipe the rate_limit_hit table before each test so counts are deterministic."""
    _purge_table()
    # Disable the throttled sweep so it does not delete buckets mid-test.
    rate_limit._last_sweep_monotonic = float("inf")
    yield
    _purge_table()
    rate_limit._last_sweep_monotonic = 0.0


def _settings(limit: int) -> object:
    return SimpleNamespace(rate_limit_per_minute=limit)


def test_two_sessions_share_one_counter() -> None:
    """Two pods (independent sessions) writing the same bucket/key must see a
    single shared count. limit=4 -> the 5th hit anywhere trips 429."""
    with patch.object(rate_limit, "get_settings", return_value=_settings(4)):
        pod_a = SessionLocal()
        pod_b = SessionLocal()
        try:
            # Pod A serves 2, pod B serves 2 -> shared count is 4, all allowed.
            for _ in range(2):
                rate_limit.rate_limit(_req("198.51.100.7"), db=pod_a)
            for _ in range(2):
                rate_limit.rate_limit(_req("198.51.100.7"), db=pod_b)

            row = pod_a.execute(
                text("SELECT hit_count FROM qb.rate_limit_hit WHERE client_key = '198.51.100.7'")
            ).scalar()
            assert row == 4  # cluster-shared, not 2+2-per-pod

            # The 5th hit (from either pod) trips 429.
            with pytest.raises(HTTPException) as exc:
                rate_limit.rate_limit(_req("198.51.100.7"), db=pod_a)
            assert exc.value.status_code == 429
        finally:
            pod_a.close()
            pod_b.close()


def test_separate_client_keys_independent() -> None:
    """Two real clients each get their own row; neither starves the other."""
    with patch.object(rate_limit, "get_settings", return_value=_settings(2)):
        db = SessionLocal()
        try:
            for ip in ("203.0.113.1", "203.0.113.2"):
                rate_limit.rate_limit(_req(ip), db=db)
                rate_limit.rate_limit(_req(ip), db=db)
            rows = {
                r[0]: r[1]
                for r in db.execute(
                    text("SELECT client_key, hit_count FROM qb.rate_limit_hit ORDER BY client_key")
                )
            }
            assert rows == {"203.0.113.1": 2, "203.0.113.2": 2}
        finally:
            db.close()


def test_sweep_deletes_aged_buckets() -> None:
    """Buckets older than the cutoff are removable; the sweep DELETE works."""
    rate_limit._last_sweep_monotonic = 0.0
    db = SessionLocal()
    try:
        # Insert two buckets: one current, one ancient.
        current = __import__("time").time() // 60
        db.execute(
            text(
                "INSERT INTO qb.rate_limit_hit (bucket, client_key, hit_count) VALUES"
                " (:cur, 'sweep-test', 5), (:old, 'sweep-test', 99)"
            ),
            {"cur": current, "old": current - 100},
        )
        db.commit()
        # Force the sweep to run with a real cutoff.
        rate_limit._maybe_sweep(db)
        db.commit()
        remaining = {
            r[0]
            for r in db.execute(
                text("SELECT bucket FROM qb.rate_limit_hit WHERE client_key = 'sweep-test'")
            )
        }
        assert current in remaining
        assert (current - 100) not in remaining  # aged bucket swept
    finally:
        db.close()
