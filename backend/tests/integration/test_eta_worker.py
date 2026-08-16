"""Integration tests for the ETA worker core: reservation, execution-state rollup,
dependency gating, and cascade cancellation against the live dev DB.

Skipped when the DB is unreachable.
"""

from __future__ import annotations


import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.engine_runtime import pick
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
    Document,
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
    def _wipe_execs() -> None:
        session.execute(Job.__table__.delete().where(Job.execution_id.in_(test_exec_ids)))
        session.execute(EtaExecution.__table__.delete().where(EtaExecution.id.in_(test_exec_ids)))
        session.commit()

    pick(bool(test_exec_ids), _wipe_execs, lambda: None)
    # Also clean any standalone smoke jobs
    session.execute(Job.__table__.delete().where(Job.name.like("test.worker.%")))
    # Clean liveness/reclaim test jobs + their documents (slug-prefixed).
    test_doc_ids = (
        session.execute(
            select(Document.id).where(Document.slug.like("liveness-test%"))
        ).scalars().all()
    )
    def _wipe_docs() -> None:
        session.execute(
            Job.__table__.delete().where(
                Job.payload.op("->>")("document_id").in_([str(d) for d in test_doc_ids])
            )
        )
        session.execute(Document.__table__.delete().where(Document.id.in_(test_doc_ids)))

    pick(bool(test_doc_ids), _wipe_docs, lambda: None)
    session.commit()
    session.close()


@pytest.fixture()
def iso_db():
    """Session whose entire transaction is rolled back at teardown.

    Reservation is global (picks the oldest eligible job across the whole table),
    so on a shared dev DB real queued app jobs would be reserved instead of the
    test's. This fixture lets a test hide those competing jobs *within its own
    transaction* (restored on rollback) so it sees only its DAG — without ever
    disturbing the real jobs, since the changes are never committed.

    Parked DAGs are committed on a side session (see ``_submit_parked_dag``); track
    their ids on ``iso_db.created`` so teardown can delete them.
    """
    session = SessionLocal()
    session.created = []  # type: ignore[attr-defined]
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        created = getattr(session, "created", [])

        def _wipe_created() -> None:
            cleanup = SessionLocal()
            try:
                cleanup.execute(Job.__table__.delete().where(Job.execution_id.in_(created)))
                cleanup.execute(
                    EtaExecution.__table__.delete().where(EtaExecution.id.in_(created))
                )
                cleanup.commit()
            finally:
                cleanup.close()

        pick(bool(created), _wipe_created, lambda: None)


def _hide_competing_queued_jobs(session, *, keep_execution_id=None) -> None:
    # Uncommitted: only visible inside this transaction, undone by rollback.
    stmt = Job.__table__.update().where(Job.status == JobStatus.queued)
    stmt = pick(
        keep_execution_id is not None,
        lambda s=stmt: s.where(Job.execution_id != keep_execution_id),
        lambda s=stmt: s,
    )
    session.execute(stmt.values(status=JobStatus.cancelled))


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

def _submit_parked_dag(spec: EtaDagSpec, iso_db) -> EtaExecution:
    """Commit a DAG with far ``run_after`` so live workers will not claim it."""
    from datetime import datetime, timedelta, timezone

    far = datetime.now(timezone.utc) + timedelta(days=30)
    parked = EtaDagSpec(
        name=spec.name,
        nodes=[
            EtaDagNode(
                key=n.key,
                name=n.name,
                payload=n.payload,
                workload=n.workload,
                priority=n.priority,
                run_after=far,
            )
            for n in spec.nodes
        ],
        edges=list(spec.edges),
        priority=spec.priority,
    )
    session = SessionLocal()
    try:
        execution = submit_dag(session, spec=parked)
        iso_db.created.append(execution.id)
        return execution
    finally:
        session.close()


def _release_parked_jobs_locally(session, execution_id) -> None:
    """Clear ``run_after`` only inside this uncommitted transaction."""
    session.execute(
        Job.__table__.update()
        .where(Job.execution_id == execution_id)
        .values(run_after=None)
    )
    session.flush()


def test_reservation_skips_job_with_unsatisfied_dependency(iso_db) -> None:
    """A job whose dependency hasn't succeeded must NOT be reserved."""
    execution = _submit_parked_dag(EtaDagSpec(
        name="wtest-gating",
        nodes=[_node("a"), _node("b")],
        edges=[EtaDagEdge("a", "b")],
    ), iso_db)
    _hide_competing_queued_jobs(iso_db, keep_execution_id=execution.id)
    _release_parked_jobs_locally(iso_db, execution.id)
    jobs = {j.node_key: j for j in iso_db.query(Job).filter(Job.execution_id == execution.id).all()}

    # 'a' has no deps → reservable.
    reserved = _reserve_next_job(iso_db)
    assert reserved is not None
    assert str(reserved[0]) == str(jobs["a"].id)

    # 'a' is now running, 'b' is blocked on 'a' → nothing else reservable.
    reserved2 = _reserve_next_job(iso_db)
    assert reserved2 is None


def test_reservation_picks_dependency_after_satisfied(iso_db) -> None:
    execution = _submit_parked_dag(EtaDagSpec(
        name="wtest-gating-ok",
        nodes=[_node("a"), _node("b")],
        edges=[EtaDagEdge("a", "b")],
    ), iso_db)
    _hide_competing_queued_jobs(iso_db, keep_execution_id=execution.id)
    _release_parked_jobs_locally(iso_db, execution.id)
    jobs = {j.node_key: j for j in iso_db.query(Job).filter(Job.execution_id == execution.id).all()}

    # Reserve 'a'
    reserved = _reserve_next_job(iso_db)
    assert str(reserved[0]) == str(jobs["a"].id)

    # Mark 'a' succeeded (within this transaction)
    iso_db.get(Job, jobs["a"].id).status = JobStatus.succeeded
    iso_db.flush()

    # Now 'b' should be reservable
    reserved2 = _reserve_next_job(iso_db)
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
    """An orphaned 'running' job (lease expired) must be reclaimed to 'queued'."""
    from datetime import datetime, timedelta, timezone

    from app.eta.stale_jobs import STALE_RUNNING_TIMEOUT_SECONDS, reclaim_stale_jobs_sync

    # Lease expired in the past → orphaned.
    orphan = Job(
        name="test.worker.reclaim_target",
        workload=JobWorkload.cpu,
        status=JobStatus.running,
        locked_by="dead-worker-uuid",
        locked_at=datetime.now(timezone.utc) - timedelta(seconds=STALE_RUNNING_TIMEOUT_SECONDS + 60),
        lease_deadline=datetime.now(timezone.utc) - timedelta(seconds=60),
        heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        payload={},
    )
    db.add(orphan)
    db.commit()

    reclaimed = reclaim_stale_jobs_sync(db, workloads=[JobWorkload.cpu.value])
    db.commit()
    assert reclaimed >= 1

    db.refresh(orphan)
    assert orphan.status == JobStatus.queued
    assert orphan.locked_by is None
    assert orphan.locked_at is None
    assert orphan.lease_deadline is None


def test_reclaim_does_not_touch_live_heartbeat_job(db) -> None:
    """A job with a fresh heartbeat (lease far in the future) must NOT be
    reclaimed, even if its locked_at is very old — the heartbeat is the truth."""
    from datetime import datetime, timedelta, timezone

    from app.eta.lease import lease_deadline_from
    from app.eta.stale_jobs import reclaim_stale_jobs_sync

    live = Job(
        name="test.worker.heartbeat_live",
        workload=JobWorkload.cpu,
        status=JobStatus.running,
        locked_by="live-worker-uuid",
        # locked_at is ancient (old slow job) — but the lease is fresh:
        locked_at=datetime.now(timezone.utc) - timedelta(hours=1),
        heartbeat_at=datetime.now(timezone.utc),
        lease_deadline=lease_deadline_from(),  # now + LEASE_DURATION
        payload={},
    )
    db.add(live)
    db.commit()

    reclaim_stale_jobs_sync(db, workloads=[JobWorkload.cpu.value])
    db.commit()
    db.refresh(live)

    assert live.status == JobStatus.running  # untouched — heartbeat is live
    assert live.locked_by == "live-worker-uuid"


def test_reclaim_does_not_touch_recent_running_job(db) -> None:
    """A job that was reserved just moments ago (no lease yet) must NOT be reclaimed."""
    from datetime import datetime, timezone

    from app.eta.stale_jobs import reclaim_stale_jobs_sync

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

    reclaim_stale_jobs_sync(db, workloads=[JobWorkload.cpu.value])
    db.commit()
    db.refresh(fresh)

    assert fresh.status == JobStatus.running  # untouched
    assert fresh.locked_by == "live-worker-uuid"


def test_reclaim_marks_exhausted_orphans_failed(db) -> None:
    """A stale running job that already hit max_attempts must fail, not re-queue."""
    from datetime import datetime, timedelta, timezone

    from app.eta.stale_jobs import reclaim_stale_jobs_sync

    orphan = Job(
        name="test.worker.exhausted_orphan",
        workload=JobWorkload.cpu,
        status=JobStatus.running,
        locked_by="dead-worker-uuid",
        locked_at=datetime.now(timezone.utc) - timedelta(hours=1),
        lease_deadline=datetime.now(timezone.utc) - timedelta(seconds=60),
        attempts=3,
        max_attempts=3,
        payload={},
    )
    db.add(orphan)
    db.commit()

    reclaimed = reclaim_stale_jobs_sync(db, workloads=[JobWorkload.cpu.value])
    db.commit()
    assert reclaimed >= 1

    db.refresh(orphan)
    assert orphan.status == JobStatus.failed
    assert orphan.locked_by is None
    assert orphan.locked_at is None


def test_reclaim_marks_exhausted_orphans_failed_for_io_too(db) -> None:
    """Parity: an exhausted orphan in the IO workload is also failed (the old
    IO reaper requeued forever — this is the fix for that asymmetry)."""
    from datetime import datetime, timedelta, timezone

    from app.eta.stale_jobs import reclaim_stale_jobs_sync

    orphan = Job(
        name="test.worker.exhausted_io_orphan",
        workload=JobWorkload.io,
        status=JobStatus.running,
        locked_by="dead-io-worker",
        locked_at=datetime.now(timezone.utc) - timedelta(hours=1),
        lease_deadline=datetime.now(timezone.utc) - timedelta(seconds=60),
        attempts=3,
        max_attempts=3,
        payload={},
    )
    db.add(orphan)
    db.commit()

    reclaim_stale_jobs_sync(db, workloads=[JobWorkload.io.value])
    db.commit()
    db.refresh(orphan)

    assert orphan.status == JobStatus.failed  # NOT requeued forever


def test_reclaim_does_not_increment_attempts(db) -> None:
    """Reclaim must NOT cost an attempt — only a real handler exception does.
    A job orphaned by deploys is not auto-failed for that."""
    from datetime import datetime, timedelta, timezone

    from app.eta.stale_jobs import reclaim_stale_jobs_sync

    orphan = Job(
        name="test.worker.no_attempt_bump",
        workload=JobWorkload.cpu,
        status=JobStatus.running,
        locked_by="dead-worker-uuid",
        locked_at=datetime.now(timezone.utc) - timedelta(hours=1),
        lease_deadline=datetime.now(timezone.utc) - timedelta(seconds=60),
        attempts=1,
        max_attempts=3,
        payload={},
    )
    db.add(orphan)
    db.commit()

    reclaim_stale_jobs_sync(db, workloads=[JobWorkload.cpu.value])
    db.commit()
    db.refresh(orphan)

    assert orphan.status == JobStatus.queued
    assert orphan.attempts == 1  # unchanged — reclaim is free


def test_reclaim_only_affects_own_workload(db) -> None:
    """The CPU worker's reclaim must not touch IO jobs (and vice-versa)."""
    from datetime import datetime, timedelta, timezone

    from app.eta.stale_jobs import reclaim_stale_jobs_sync

    # Stale IO job — should NOT be reclaimed by the CPU worker
    stale_io = Job(
        name="test.worker.stale_io",
        workload=JobWorkload.io,
        status=JobStatus.running,
        locked_by="dead-io-worker",
        locked_at=datetime.now(timezone.utc) - timedelta(hours=1),
        lease_deadline=datetime.now(timezone.utc) - timedelta(seconds=60),
        payload={},
    )
    db.add(stale_io)
    db.commit()

    reclaim_stale_jobs_sync(db, workloads=[JobWorkload.cpu.value])
    db.commit()
    db.refresh(stale_io)

    assert stale_io.status == JobStatus.running  # IO job untouched by CPU reclaim


def test_reclaim_across_all_workloads_recovers_both(db) -> None:
    """The scheduler-driven reclaim passes BOTH workloads and recovers orphans
    in each — closes the 'all workers down' gap."""
    from datetime import datetime, timedelta, timezone

    from app.eta.schedules.jobs_reclaim import run_jobs_reclaim

    orphan_io = Job(
        name="test.worker.orphan_io",
        workload=JobWorkload.io,
        status=JobStatus.running,
        locked_by="dead-io",
        locked_at=datetime.now(timezone.utc) - timedelta(hours=1),
        lease_deadline=datetime.now(timezone.utc) - timedelta(seconds=60),
        payload={},
    )
    orphan_cpu = Job(
        name="test.worker.orphan_cpu",
        workload=JobWorkload.cpu,
        status=JobStatus.running,
        locked_by="dead-cpu",
        locked_at=datetime.now(timezone.utc) - timedelta(hours=1),
        lease_deadline=datetime.now(timezone.utc) - timedelta(seconds=60),
        payload={},
    )
    db.add_all([orphan_io, orphan_cpu])
    db.commit()

    run_jobs_reclaim()  # runs against all workloads, no worker pod needed
    db.refresh(orphan_io)
    db.refresh(orphan_cpu)

    assert orphan_io.status == JobStatus.queued
    assert orphan_cpu.status == JobStatus.queued


# --------------------------------------------------------------------------- #
# Lease heartbeat renewal
# --------------------------------------------------------------------------- #

def test_renew_lease_extends_deadline_and_sets_heartbeat(db) -> None:
    """A heartbeat renew must push lease_deadline out and stamp heartbeat_at."""
    from datetime import datetime, timedelta, timezone

    from app.eta.lease import LEASE_DURATION_SECONDS, renew_lease_sync

    job = Job(
        name="test.worker.heartbeat_renew",
        workload=JobWorkload.cpu,
        status=JobStatus.running,
        locked_by="live-worker",
        locked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        lease_deadline=datetime.now(timezone.utc) - timedelta(minutes=1),  # about to expire
        payload={},
    )
    db.add(job)
    db.commit()

    before = datetime.now(timezone.utc)
    renewed = renew_lease_sync(db, job.id)
    db.commit()
    assert renewed is True
    db.refresh(job)

    assert job.heartbeat_at is not None
    assert job.heartbeat_at >= before
    assert job.lease_deadline is not None
    # Deadline pushed out by ~LEASE_DURATION from now.
    assert job.lease_deadline >= before + timedelta(seconds=LEASE_DURATION_SECONDS - 5)


def test_renew_lease_noops_on_terminal_job(db) -> None:
    """A heartbeat renew on a job that already finished must be a safe no-op."""
    from app.eta.lease import renew_lease_sync

    job = Job(
        name="test.worker.heartbeat_noop",
        workload=JobWorkload.cpu,
        status=JobStatus.succeeded,  # already done
        payload={},
    )
    db.add(job)
    db.commit()

    renewed = renew_lease_sync(db, job.id)
    db.commit()
    assert renewed is False


# --------------------------------------------------------------------------- #
# Lease-aware generate.questions reclaim + liveness SQL
# --------------------------------------------------------------------------- #

def _make_test_doc(db) -> Document:
    """A throwaway document whose slug matches the ``db`` fixture cleanup."""
    doc = Document(
        slug=f"liveness-test-{uuid.uuid4().hex[:8]}",
        filename="t.pdf",
        content_type="application/pdf",
        size_bytes=1,
        storage_key="x",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def test_reclaim_generate_jobs_skips_valid_lease(db) -> None:
    """A generate.questions job with an unexpired lease must NOT be reclaimed,
    even if its locked_at is ancient — the lease is the truth (no split-brain)."""
    from datetime import datetime, timedelta, timezone

    from app.eta.lease import lease_deadline_from
    from app.services.question_pool import _reclaim_stale_generate_jobs

    doc = _make_test_doc(db)
    live = Job(
        name="generate.questions",
        workload=JobWorkload.cpu,
        status=JobStatus.running,
        locked_by="live-worker",
        locked_at=datetime.now(timezone.utc) - timedelta(hours=1),  # ancient
        heartbeat_at=datetime.now(timezone.utc),  # fresh heartbeat
        lease_deadline=lease_deadline_from(),  # valid lease — now + LEASE_DURATION
        payload={"document_id": str(doc.id)},
    )
    db.add(live)
    db.commit()

    reclaimed = _reclaim_stale_generate_jobs(db)
    db.commit()
    db.refresh(live)

    assert reclaimed == 0
    assert live.status == JobStatus.running  # untouched
    assert live.locked_by == "live-worker"


def test_reclaim_generate_jobs_requeues_expired_lease_and_clears_fields(db) -> None:
    """An orphaned generate.questions job (lease expired) is requeued and its
    lease/lock fields cleared — while result/checkpoint is preserved."""
    from datetime import datetime, timedelta, timezone

    from app.services.question_pool import _reclaim_stale_generate_jobs

    doc = _make_test_doc(db)
    orphan = Job(
        name="generate.questions",
        workload=JobWorkload.cpu,
        status=JobStatus.running,
        locked_by="dead-worker",
        locked_at=datetime.now(timezone.utc) - timedelta(hours=1),
        heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=1),
        lease_deadline=datetime.now(timezone.utc) - timedelta(seconds=60),  # expired
        result={"checkpoint": "saved-sequence"},  # must survive the reclaim
        payload={"document_id": str(doc.id)},
    )
    db.add(orphan)
    db.commit()

    reclaimed = _reclaim_stale_generate_jobs(db)
    db.commit()
    db.refresh(orphan)

    assert reclaimed >= 1
    assert orphan.status == JobStatus.queued
    assert orphan.locked_by is None
    assert orphan.locked_at is None
    assert orphan.heartbeat_at is None
    assert orphan.lease_deadline is None
    assert orphan.result == {"checkpoint": "saved-sequence"}  # preserved


def test_has_active_ingest_jobs_sees_valid_lease_job_as_active(db) -> None:
    """A long ingest job with a valid lease counts as active — so the recovery
    schedules don't mistake it for dead and enqueue duplicate rag_window work."""
    from datetime import datetime, timedelta, timezone

    from app.eta.lease import lease_deadline_from
    from app.services.rag_window import has_active_ingest_jobs

    doc = _make_test_doc(db)
    live_ingest = Job(
        name="ingest.rag_window",
        workload=JobWorkload.cpu,
        status=JobStatus.running,
        locked_by="live-worker",
        locked_at=datetime.now(timezone.utc) - timedelta(hours=1),  # past stale window
        heartbeat_at=datetime.now(timezone.utc),  # but heartbeat is fresh
        lease_deadline=lease_deadline_from(),  # and lease is valid
        payload={"document_id": str(doc.id)},
    )
    db.add(live_ingest)
    db.commit()

    assert has_active_ingest_jobs(db, doc.id) is True


# --------------------------------------------------------------------------- #
# Scheduler registration (no DB needed)
# --------------------------------------------------------------------------- #

def test_jobs_reclaim_schedule_registered() -> None:
    """The scheduler-driven reclaim must be registered so it runs even when no
    worker pod is alive (closes the 'all workers down' recovery gap)."""
    from app.eta.scheduler_registry import list_scheduler_definitions

    import app.eta.schedules  # noqa: F401 — triggers registration

    defs = list_scheduler_definitions()
    assert "jobs.reclaim_stale" in defs
    assert defs["jobs.reclaim_stale"].every_minutes == 2

