from datetime import datetime, timedelta

from app.eta.scheduler_registry import EtaSchedulerDefinition
from app.eta.scheduler_runtime import (
    ReservedEtaSchedule,
    _reconcile_schedule_rows,
    run_due_schedules,
)
from app.models.eta_schedule import EtaSchedule


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.commit_calls = 0

    def query(self, model):
        return _FakeQuery(self.rows)

    def execute(self, statement):
        return _FakeScalarResult(self.rows)

    def commit(self):
        self.commit_calls += 1

    def get(self, model, schedule_id):
        for row in self.rows:
            if row.id == schedule_id:
                return row
        return None


def test_reconcile_schedule_rows_inserts_and_orphans():
    now = datetime(2026, 3, 7, 12, 0, 0)
    existing = [
        EtaSchedule(
            schedule_key="stale.schedule",
            enabled=True,
            is_orphaned=False,
            next_run_at=now,
        )
    ]
    definitions = [
        EtaSchedulerDefinition(
            key="new.schedule",
            fn=lambda: None,
            every_minutes=10,
        )
    ]

    rows = _reconcile_schedule_rows(definitions=definitions, existing_rows=existing, now=now)
    by_key = {row.schedule_key: row for row in rows}

    assert by_key["new.schedule"].next_run_at == now + timedelta(minutes=10)
    assert by_key["stale.schedule"].enabled is False
    assert by_key["stale.schedule"].is_orphaned is True


def test_run_due_schedules_executes_and_advances(monkeypatch):
    now = datetime(2026, 3, 7, 12, 0, 0)
    executed = {"count": 0}

    schedule = EtaSchedule(
        schedule_key="test.schedule",
        enabled=True,
        is_orphaned=False,
        next_run_at=now - timedelta(minutes=1),
        last_error="old error",
    )
    fake_db = _FakeDB([schedule])

    definition = EtaSchedulerDefinition(
        key="test.schedule",
        fn=lambda: executed.__setitem__("count", executed["count"] + 1),
        every_minutes=10,
    )
    monkeypatch.setattr(
        "app.eta.scheduler_runtime.list_scheduler_definitions",
        lambda: {"test.schedule": definition},
    )

    triggered = run_due_schedules(fake_db, now=now)

    assert triggered == 1
    assert executed["count"] == 1
    assert schedule.last_error is None
    assert schedule.last_run_at == now
    assert schedule.next_run_at == now + timedelta(minutes=10)
    assert fake_db.commit_calls == 2


def test_run_due_schedules_disables_missing_definition():
    now = datetime(2026, 3, 7, 12, 0, 0)
    schedule = EtaSchedule(
        schedule_key="missing.schedule",
        enabled=True,
        is_orphaned=False,
        next_run_at=now - timedelta(minutes=1),
    )
    fake_db = _FakeDB([schedule])

    triggered = run_due_schedules(fake_db, now=now)

    assert triggered == 1
    assert schedule.enabled is False
    assert schedule.is_orphaned is True
    assert schedule.last_error == "Scheduler definition no longer exists"
    assert fake_db.commit_calls == 1


def test_run_due_schedules_reserves_before_execution(monkeypatch):
    now = datetime(2026, 3, 7, 12, 0, 0)
    schedule = EtaSchedule(
        schedule_key="test.schedule",
        enabled=True,
        is_orphaned=False,
        next_run_at=now - timedelta(minutes=1),
    )
    schedule.id = "schedule-1"
    fake_db = _FakeDB([schedule])
    observed_next_run = {"value": None}

    def _reserve(db, *, now):
        schedule.next_run_at = now + timedelta(minutes=10)
        return (
            [
                ReservedEtaSchedule(
                    schedule_id="schedule-1",
                    schedule_key="test.schedule",
                    definition=EtaSchedulerDefinition(
                        key="test.schedule",
                        fn=lambda: observed_next_run.__setitem__("value", schedule.next_run_at),
                        every_minutes=10,
                    ),
                )
            ],
            1,
        )

    monkeypatch.setattr("app.eta.scheduler_runtime._reserve_due_schedules", _reserve)

    triggered = run_due_schedules(fake_db, now=now)

    assert triggered == 1
    assert observed_next_run["value"] == now + timedelta(minutes=10)
