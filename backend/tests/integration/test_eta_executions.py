"""Integration tests for the ETA DAG/execution engine against the live dev DB.

These require a reachable Postgres (the dev DB on localhost:5453) and are
skipped automatically when it is unavailable. Run with the dev deps up:

    ./scripts/dev.sh deps start
    ~/.venv/zivo/bin/python -m pytest tests/integration -q
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.engine_runtime import pick
from app.eta.executions import (
    cancel_execution,
    get_execution,
    list_execution_jobs,
    retry_failed_jobs,
    submit_dag,
    submit_group,
)
from app.eta.registry import eta
from app.eta.spec import EtaDagEdge, EtaDagNode, EtaDagSpec
from app.models import (
    EtaExecution,
    EtaExecutionMode,
    EtaExecutionStatus,
    EtaJobDependency,
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


# Register a no-op handler so build_job can resolve workload/priority from the registry.
@eta(name="test.integration.noop", workload=JobWorkload.io)
def _noop(payload):  # noqa: ANN201
    return {"ok": True}


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    # Cleanup any test executions + their jobs (jobs first to satisfy FK).
    test_exec_ids = (
        session.execute(
            select(EtaExecution.id).where(EtaExecution.name.like("itest-%"))
        ).scalars().all()
    )
    def _wipe_execs() -> None:
        session.execute(Job.__table__.delete().where(Job.execution_id.in_(test_exec_ids)))
        session.execute(EtaExecution.__table__.delete().where(EtaExecution.id.in_(test_exec_ids)))
        session.commit()

    pick(bool(test_exec_ids), _wipe_execs, lambda: None)
    session.close()


def _node(key: str, **kw) -> EtaDagNode:
    return EtaDagNode(key=key, name="test.integration.noop", payload={"step": key}, **kw)


# --------------------------------------------------------------------------- #
# submit_dag
# --------------------------------------------------------------------------- #

def test_submit_dag_creates_execution_and_jobs(db) -> None:
    spec = EtaDagSpec(
        name="itest-linear",
        nodes=[_node("a"), _node("b"), _node("c")],
        edges=[EtaDagEdge("a", "b"), EtaDagEdge("b", "c")],
    )
    execution = submit_dag(db, spec=spec)

    assert execution.id is not None
    assert execution.total_jobs == 3
    assert execution.status == EtaExecutionStatus.queued
    assert execution.mode == EtaExecutionMode.dag
    assert len(list_execution_jobs(db, execution.id)) == 3


def test_dag_creates_dependency_edges(db) -> None:
    spec = EtaDagSpec(
        name="itest-deps",
        nodes=[_node("a"), _node("b")],
        edges=[EtaDagEdge("a", "b")],
    )
    execution = submit_dag(db, spec=spec)
    jobs = list_execution_jobs(db, execution.id)
    assert len(jobs) == 2
    job_ids = {j.id for j in jobs}
    deps = (
        db.query(EtaJobDependency)
        .filter(EtaJobDependency.job_id.in_(job_ids))
        .all()
    )
    assert len(deps) == 1
    assert deps[0].depends_on_job_id in job_ids


def test_parent_job_ids_propagated_to_payload(db) -> None:
    spec = EtaDagSpec(
        name="itest-parents",
        nodes=[_node("a"), _node("b")],
        edges=[EtaDagEdge("a", "b")],
    )
    execution = submit_dag(db, spec=spec)
    jobs = {j.node_key: j for j in list_execution_jobs(db, execution.id)}

    assert "parent_job_ids" not in (jobs["a"].payload or {})
    assert "parent_job_ids" in (jobs["b"].payload or {})
    assert str(jobs["a"].id) in jobs["b"].payload["parent_job_ids"]


def test_dag_inherits_execution_priority(db) -> None:
    from app.models import JobPriority

    spec = EtaDagSpec(
        name="itest-priority",
        nodes=[_node("a")],
        priority=JobPriority.HIGH,
    )
    execution = submit_dag(db, spec=spec)
    job = list_execution_jobs(db, execution.id)[0]
    assert job.priority == JobPriority.HIGH


# --------------------------------------------------------------------------- #
# submit_group
# --------------------------------------------------------------------------- #

def test_submit_group_creates_parallel_jobs(db) -> None:
    execution = submit_group(
        db,
        name="itest-group",
        job_name="test.integration.noop",
        items=[{"i": 1}, {"i": 2}, {"i": 3}],
    )
    assert execution.total_jobs == 3
    assert execution.mode == EtaExecutionMode.group
    assert len(list_execution_jobs(db, execution.id)) == 3
    # No dependencies in a group
    assert db.query(EtaJobDependency).filter(
        EtaJobDependency.job_id.in_([j.id for j in list_execution_jobs(db, execution.id)])
    ).count() == 0


# --------------------------------------------------------------------------- #
# cancel_execution
# --------------------------------------------------------------------------- #

def test_cancel_execution_marks_queued_jobs_cancelled(db) -> None:
    execution = submit_dag(db, spec=EtaDagSpec(
        name="itest-cancel", nodes=[_node("a"), _node("b")],
    ))
    cancelled = cancel_execution(db, execution.id)

    assert cancelled.status == EtaExecutionStatus.cancelled
    jobs = list_execution_jobs(db, execution.id)
    assert all(j.status == JobStatus.cancelled for j in jobs)


def test_cancel_execution_cancels_running_jobs(db) -> None:
    execution = submit_dag(db, spec=EtaDagSpec(
        name="itest-cancel-running", nodes=[_node("a"), _node("b")],
    ))
    running = list_execution_jobs(db, execution.id)[0]
    running.status = JobStatus.running
    running.locked_by = "itest-worker"
    db.commit()

    cancelled = cancel_execution(db, execution.id)
    assert cancelled.status == EtaExecutionStatus.cancelled
    jobs = list_execution_jobs(db, execution.id)
    assert all(j.status == JobStatus.cancelled for j in jobs)
    assert all(j.locked_by is None for j in jobs)


def test_cancel_unknown_execution_returns_none(db) -> None:
    assert cancel_execution(db, uuid.uuid4()) is None


# --------------------------------------------------------------------------- #
# retry_failed_jobs
# --------------------------------------------------------------------------- #

def test_retry_failed_jobs_resets_failed_to_queued(db) -> None:
    execution = submit_dag(db, spec=EtaDagSpec(
        name="itest-retry", nodes=[_node("a"), _node("b")],
    ))
    # Manually fail one job to simulate a failure
    job = list_execution_jobs(db, execution.id)[0]
    job.status = JobStatus.failed
    job.error = "boom"
    db.commit()

    result = retry_failed_jobs(db, execution.id)
    assert result is not None
    _exec, count = result
    assert count == 1

    refreshed = db.get(Job, job.id)
    assert refreshed.status == JobStatus.queued
    assert refreshed.error is None
    assert refreshed.attempts == 0


def test_retry_returns_zero_when_no_failures(db) -> None:
    execution = submit_dag(db, spec=EtaDagSpec(
        name="itest-retry-none", nodes=[_node("a")],
    ))
    result = retry_failed_jobs(db, execution.id)
    assert result is not None
    assert result[1] == 0


# --------------------------------------------------------------------------- #
# get_execution
# --------------------------------------------------------------------------- #

def test_get_execution_returns_none_for_unknown(db) -> None:
    assert get_execution(db, uuid.uuid4()) is None
