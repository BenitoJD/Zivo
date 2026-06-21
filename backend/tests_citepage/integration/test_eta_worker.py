"""Integration tests for the ETA worker core: reservation, execution-state rollup,
dependency gating, and cascade cancellation against the live dev DB.

Skipped when the DB is unreachable.
"""

from __future__ import annotations


import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.eta.execution_state import (
    _resolve_execution_status,
    cancel_descendants_sync,
    update_execution_state_sync,
)
from app.eta.executions import submit_dag
from app.eta.registry import eta
from app.eta.spec import EtaDagEdge, EtaDagNode, EtaDagSpec
from app.eta.worker import _process_job, _reserve_next_job
from app.models import (
    EtaExecution,
    EtaExecutionStatus,
    Job,
    JobStatus,
    JobWorkload,
)


def _db_reachable() -> bool:
    try:
        db = SessionLocal()
        db.execute(select(1))
        db.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="dev DB not reachable")


@eta(name="test.worker.success", workload=JobWorkload.cpu)
def _success_handler(payload):  # noqa: ANN201
    return {"processed": payload.get("val", 0)}


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    test_exec_ids = (
        session.execute(
            select(EtaExecution.id).where(EtaExecution.name.like("wtest-%"))
        ).scalars().all()
    )
    if test_exec_ids:
        session.execute(Job.__table__.delete().where(Job.execution_id.in_(test_exec_ids)))
        session.execute(EtaExecution.__table__.delete().where(EtaExecution.id.in_(test_exec_ids)))
        session.commit()
    # Also clean any standalone smoke jobs
    session.execute(Job.__table__.delete().where(Job.name.like("test.worker.%")))
    session.commit()
    session.close()


def _node(key: str, **kw) -> EtaDagNode:
    return EtaDagNode(key=key, name="test.worker.success", payload={"val": 1}, **kw)


# --------------------------------------------------------------------------- #
# Execution-state rollup logic (pure function)
# --------------------------------------------------------------------------- #

def test_resolve_status_all_succeeded() -> None:
    counts = {JobStatus.succeeded: 3}
    assert _resolve_execution_status(counts, 3) == EtaExecutionStatus.succeeded


def test_resolve_status_running_in_progress() -> None:
    counts = {JobStatus.succeeded: 1, JobStatus.running: 1, JobStatus.queued: 1}
    assert _resolve_execution_status(counts, 3) == EtaExecutionStatus.running


def test_resolve_status_partial_failure() -> None:
    counts = {JobStatus.succeeded: 2, JobStatus.failed: 1}
    assert _resolve_execution_status(counts, 3) == EtaExecutionStatus.partial_failed


def test_resolve_status_all_failed() -> None:
    counts = {JobStatus.failed: 3}
    assert _resolve_execution_status(counts, 3) == EtaExecutionStatus.failed


def test_resolve_status_all_cancelled() -> None:
    counts = {JobStatus.cancelled: 2}
    assert _resolve_execution_status(counts, 2) == EtaExecutionStatus.cancelled


def test_resolve_status_empty() -> None:
    assert _resolve_execution_status({}, 0) == EtaExecutionStatus.queued


# --------------------------------------------------------------------------- #
# update_execution_state_sync (DB)
# --------------------------------------------------------------------------- #

def test_execution_state_rolls_up_to_succeeded(db) -> None:
    execution = submit_dag(db, spec=EtaDagSpec(
        name="wtest-rollup", nodes=[_node("a"), _node("b")],
    ))
    jobs = {j.node_key: j for j in db.query(Job).filter(Job.execution_id == execution.id).all()}
    jobs["a"].status = JobStatus.succeeded
    jobs["b"].status = JobStatus.succeeded
    db.commit()

    update_execution_state_sync(db, execution.id)
    db.commit()
    db.refresh(execution)

    assert execution.status == EtaExecutionStatus.succeeded
    assert execution.succeeded_jobs == 2
    assert execution.completed_at is not None


# --------------------------------------------------------------------------- #
# Dependency-gated reservation
# --------------------------------------------------------------------------- #

def test_reservation_skips_job_with_unsatisfied_dependency(db) -> None:
    """A job whose dependency hasn't succeeded must NOT be reserved."""
    execution = submit_dag(db, spec=EtaDagSpec(
        name="wtest-gating",
        nodes=[_node("a"), _node("b")],
        edges=[EtaDagEdge("a", "b")],
    ))
    jobs = {j.node_key: j for j in db.query(Job).filter(Job.execution_id == execution.id).all()}

    # 'a' has no deps → reservable. Worker reserves it (now 'running').
    with SessionLocal.begin() as reserve_db:
        reserved = _reserve_next_job(reserve_db)
    assert reserved is not None
    assert str(reserved[0]) == str(jobs["a"].id)

    # 'a' is now running, 'b' is blocked on 'a' → nothing reservable.
    with SessionLocal.begin() as reserve_db:
        reserved2 = _reserve_next_job(reserve_db)
    assert reserved2 is None


def test_reservation_picks_dependency_after_satisfied(db) -> None:
    execution = submit_dag(db, spec=EtaDagSpec(
        name="wtest-gating-ok",
        nodes=[_node("a"), _node("b")],
        edges=[EtaDagEdge("a", "b")],
    ))
    jobs = {j.node_key: j for j in db.query(Job).filter(Job.execution_id == execution.id).all()}

    # Reserve 'a'
    with SessionLocal.begin() as reserve_db:
        reserved = _reserve_next_job(reserve_db)
    assert str(reserved[0]) == str(jobs["a"].id)

    # Mark 'a' succeeded
    db.get(Job, jobs["a"].id).status = JobStatus.succeeded
    db.commit()

    # Now 'b' should be reservable
    with SessionLocal.begin() as reserve_db:
        reserved2 = _reserve_next_job(reserve_db)
    assert reserved2 is not None
    assert str(reserved2[0]) == str(jobs["b"].id)


# --------------------------------------------------------------------------- #
# Cascade cancellation
# --------------------------------------------------------------------------- #

def test_cascade_cancel_descendants_on_failure(db) -> None:
    execution = submit_dag(db, spec=EtaDagSpec(
        name="wtest-cascade",
        nodes=[_node("a"), _node("b"), _node("c")],
        edges=[EtaDagEdge("a", "b"), EtaDagEdge("b", "c")],
    ))
    jobs = {j.node_key: j for j in db.query(Job).filter(Job.execution_id == execution.id).all()}

    # Simulate 'a' failing terminally → descendants (b, c) should be cancelled
    cancel_descendants_sync(db, job_id=jobs["a"].id, error_message="a failed")
    db.commit()

    db.refresh(jobs["b"])
    db.refresh(jobs["c"])
    assert jobs["b"].status == JobStatus.cancelled
    assert jobs["c"].status == JobStatus.cancelled


# --------------------------------------------------------------------------- #
# _process_job: unknown name raises (worker catches and fails the job)
# --------------------------------------------------------------------------- #

def test_process_job_raises_for_unknown_handler(db) -> None:
    job = Job(name="test.worker.does_not_exist", workload=JobWorkload.cpu, payload={})
    db.add(job)
    db.commit()
    with pytest.raises(RuntimeError, match="Unknown job name"):
        _process_job(job.id, "test.worker.does_not_exist", {})


# --------------------------------------------------------------------------- #
# FIX #2: stale-job recovery sweep on worker startup
# --------------------------------------------------------------------------- #

def test_reclaim_stale_jobs_resets_orphaned_running(db) -> None:
    """An orphaned 'running' job (simulating a crashed worker) must be reclaimed
    back to 'queued' so it isn't silently lost forever."""
    from datetime import datetime, timedelta, timezone

    from app.eta.worker import STALE_RUNNING_TIMEOUT_SECONDS, _reclaim_stale_jobs

    # Create a job that's 'running' but with a locked_at far in the past (orphaned)
    orphan = Job(
        name="test.worker.reclaim_target",
        workload=JobWorkload.cpu,
        status=JobStatus.running,
        locked_by="dead-worker-uuid",
        locked_at=datetime.now(timezone.utc) - timedelta(seconds=STALE_RUNNING_TIMEOUT_SECONDS + 60),
        payload={},
    )
    db.add(orphan)
    db.commit()

    reclaimed = _reclaim_stale_jobs(db)
    db.commit()
    assert reclaimed >= 1

    db.refresh(orphan)
    assert orphan.status == JobStatus.queued
    assert orphan.locked_by is None
    assert orphan.locked_at is None


def test_reclaim_does_not_touch_recent_running_job(db) -> None:
    """A job that was reserved just moments ago must NOT be reclaimed."""
    from datetime import datetime, timezone

    from app.eta.worker import _reclaim_stale_jobs

    fresh = Job(
        name="test.worker.fresh_running",
        workload=JobWorkload.cpu,
        status=JobStatus.running,
        locked_by="live-worker-uuid",
        locked_at=datetime.now(timezone.utc),  # just now — not stale
        payload={},
    )
    db.add(fresh)
    db.commit()

    _reclaim_stale_jobs(db)
    db.commit()
    db.refresh(fresh)

    assert fresh.status == JobStatus.running  # untouched
    assert fresh.locked_by == "live-worker-uuid"


def test_reclaim_only_affects_own_workload(db) -> None:
    """The CPU worker's reclaim must not touch IO jobs (and vice-versa)."""
    from datetime import datetime, timedelta, timezone

    from app.eta.worker import STALE_RUNNING_TIMEOUT_SECONDS, _reclaim_stale_jobs

    # Stale IO job — should NOT be reclaimed by the CPU worker
    stale_io = Job(
        name="test.worker.stale_io",
        workload=JobWorkload.io,
        status=JobStatus.running,
        locked_by="dead-io-worker",
        locked_at=datetime.now(timezone.utc) - timedelta(seconds=STALE_RUNNING_TIMEOUT_SECONDS + 60),
        payload={},
    )
    db.add(stale_io)
    db.commit()

    _reclaim_stale_jobs(db)  # CPU worker's reclaim (WORKLOADS defaults to cpu)
    db.commit()
    db.refresh(stale_io)

    assert stale_io.status == JobStatus.running  # IO job untouched by CPU reclaim

