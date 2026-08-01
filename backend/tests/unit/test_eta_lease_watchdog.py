"""Watchdog for hung ETA jobs — a handler that never returns must not
heartbeat forever (the stale reaper would never reclaim it and the UI would
sit at "Processing…" indefinitely)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.eta.lease import job_duration_exceeded


def test_job_duration_exceeded_false_for_fresh_job() -> None:
    now = datetime.now(timezone.utc)
    assert job_duration_exceeded(now - timedelta(seconds=10), now=now) is False


def test_job_duration_exceeded_true_for_old_job() -> None:
    now = datetime.now(timezone.utc)
    assert job_duration_exceeded(now - timedelta(hours=2), now=now) is True


def test_job_duration_exceeded_false_without_start() -> None:
    assert job_duration_exceeded(None) is False


def test_job_duration_exceeded_boundary() -> None:
    now = datetime.now(timezone.utc)
    # Just under the cap (3600s default) must not trigger.
    assert job_duration_exceeded(now - timedelta(seconds=3599), now=now) is False
