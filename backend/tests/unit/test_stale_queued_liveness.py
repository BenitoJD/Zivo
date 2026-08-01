"""Regression tests for the stale-queued liveness fix.

Background: ``ACTIVE_JOB_LIVENESS_SQL`` previously treated every ``queued`` job
as "active" with no age bound. A ``queued`` row that would *never* run (a DAG
child orphaned by a cancelled parent, a row for a dead worker pool) therefore
masked its document from recovery forever — the doc froze at its last
``prep_progress`` ("Prepping X%" indefinitely). The fix bounds ``queued``
liveness by ``created_at`` (via ``stale_queued_cutoff``) and ``attempts``, while
still honoring a legitimate future ``run_after``.

These tests assert the shape of the predicate and the cutoff helpers, so a
regression that reintroduces unbounded ``queued`` liveness fails loudly without
needing a live database.
"""

from __future__ import annotations

from app.eta.stale_jobs import (
    ACTIVE_JOB_LIVENESS_SQL,
    STALE_QUEUED_TIMEOUT_SECONDS,
    stale_queued_cutoff,
    stale_running_cutoff,
)


def test_stale_queued_cutoff_defaults_to_30_minutes() -> None:
    # Documented default (ETA_STALE_QUEUED_TIMEOUT=1800). Generous vs. real queue
    # backpressure; only catches jobs that will never run.
    assert STALE_QUEUED_TIMEOUT_SECONDS == 1800.0


def test_stale_queued_cutoff_is_behind_now() -> None:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    cutoff = stale_queued_cutoff(now)
    assert (now - cutoff).total_seconds() == STALE_QUEUED_TIMEOUT_SECONDS


def test_liveness_sql_bounds_queued_by_created_at() -> None:
    # The age bound is the whole fix: a queued job must reference created_at or
    # run_after to count as live. Assert the queued branch is gated, not bare.
    assert "j.status = 'queued'" in ACTIVE_JOB_LIVENESS_SQL
    # The bound on created_at is present and uses the new bind param.
    assert "j.created_at >= :queued_cutoff" in ACTIVE_JOB_LIVENESS_SQL
    # A legitimately deferred job survives regardless of age.
    assert "j.run_after" in ACTIVE_JOB_LIVENESS_SQL
    # An exhausted queued job (attempts >= max_attempts) is never live — it would
    # otherwise mask its document forever after burning all retries.
    assert "j.attempts < j.max_attempts" in ACTIVE_JOB_LIVENESS_SQL


def test_liveness_sql_binds_queued_cutoff_param() -> None:
    # Every caller of ACTIVE_JOB_LIVENESS_SQL must supply :queued_cutoff. Assert
    # the param name is declared in the SQL so a mismatch is caught at bind time.
    assert ":queued_cutoff" in ACTIVE_JOB_LIVENESS_SQL


def test_liveness_sql_preserves_running_lease_branch() -> None:
    # The running/lease logic is unchanged — long heartbeating jobs stay live.
    assert "j.lease_deadline >= NOW()" in ACTIVE_JOB_LIVENESS_SQL
    assert "j.locked_at >= :stale_cutoff" in ACTIVE_JOB_LIVENESS_SQL


def test_both_cutoff_helpers_exported() -> None:
    # Callers import these together; assert both exist on the module.
    assert callable(stale_running_cutoff)
    assert callable(stale_queued_cutoff)


def test_queued_branch_uses_AND_conjunction_not_bare_or() -> None:
    # Guard against a regression that flattens the queued branch to a bare
    # `status='queued'` OR (age) — which would re-admit ancient queued rows.
    # The status test and the age test must be conjoined inside the queued branch.
    queued_branch = ACTIVE_JOB_LIVENESS_SQL.split("j.status = 'queued'", 1)[1]
    queued_branch = queued_branch.split("j.status = 'running'", 1)[0]
    assert "AND" in queued_branch, "queued status must be AND-gated with an age bound"
