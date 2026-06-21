"""Integration tests for the ETA scheduler against the live dev DB.

Requires Postgres (dev deps) and migration 004_add_eta_schedules applied.
Skipped automatically when the DB is unreachable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.eta.scheduler_registry import EtaSchedulerDefinition
from app.eta.scheduler_runtime import (
    EtaSchedulerService,
    reconcile_scheduler_definitions,
    run_due_schedules,
)
from app.models.eta_schedule import EtaSchedule

TEST_SCHEDULE_KEY = "integration.test.schedule"


def _db_reachable() -> bool:
    try:
        db = SessionLocal()
        db.execute(select(1))
        db.close()
        return True
    except Exception:
        return False


def _eta_schedules_table_exists() -> bool:
    try:
        db = SessionLocal()
        db.execute(select(EtaSchedule).limit(1))
        db.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable() or not _eta_schedules_table_exists(),
    reason="dev DB or eta_schedules table not available (run deps + alembic upgrade head)",
)


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.execute(
            delete(EtaSchedule).where(EtaSchedule.schedule_key.like("integration.%"))
        )
        session.commit()
        session.close()


@pytest.fixture()
def registered_schedule(monkeypatch):
    executed: list[int] = []

    def _callback() -> None:
        executed.append(1)

    definition = EtaSchedulerDefinition(
        key=TEST_SCHEDULE_KEY,
        fn=_callback,
        every_minutes=10,
    )
    registry = {TEST_SCHEDULE_KEY: definition}
    monkeypatch.setattr(
        "app.eta.scheduler_runtime.list_scheduler_definitions",
        lambda: registry,
    )
    monkeypatch.setattr(
        "app.eta.scheduler_registry.list_scheduler_definitions",
        lambda: registry,
    )
    return executed


def test_reconcile_inserts_schedule_row(db, registered_schedule) -> None:
    now = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)

    reconcile_scheduler_definitions(db, now=now)

    row = db.execute(
        select(EtaSchedule).where(EtaSchedule.schedule_key == TEST_SCHEDULE_KEY)
    ).scalar_one()
    assert row.enabled is True
    assert row.is_orphaned is False
    assert row.next_run_at == now + timedelta(minutes=10)


def test_reconcile_orphans_removed_definitions(db, registered_schedule) -> None:
    stale = EtaSchedule(
        schedule_key="integration.stale.schedule",
        enabled=True,
        is_orphaned=False,
        next_run_at=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
    )
    db.add(stale)
    db.commit()

    reconcile_scheduler_definitions(db, now=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc))

    db.refresh(stale)
    assert stale.enabled is False
    assert stale.is_orphaned is True


def test_run_due_schedules_executes_and_persists(db, registered_schedule) -> None:
    now = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
    schedule = EtaSchedule(
        schedule_key=TEST_SCHEDULE_KEY,
        enabled=True,
        is_orphaned=False,
        next_run_at=now - timedelta(minutes=1),
        last_error="previous failure",
    )
    db.add(schedule)
    db.commit()

    triggered = run_due_schedules(db, now=now)

    assert triggered == 1
    assert registered_schedule == [1]
    db.refresh(schedule)
    assert schedule.last_error is None
    assert schedule.last_run_at == now
    assert schedule.next_run_at == now + timedelta(minutes=10)


def test_scheduler_service_reconciles_on_start(db, registered_schedule, monkeypatch) -> None:
    monkeypatch.setenv("ETA_SCHEDULER_ENABLED", "true")
    service = EtaSchedulerService(poll_interval_seconds=0.01)

    service.start()
    try:
        service._stop_event.wait(0.5)
        verify = SessionLocal()
        try:
            row = verify.execute(
                select(EtaSchedule).where(EtaSchedule.schedule_key == TEST_SCHEDULE_KEY)
            ).scalar_one_or_none()
        finally:
            verify.close()
        assert row is not None
        assert row.enabled is True
    finally:
        service.stop()
