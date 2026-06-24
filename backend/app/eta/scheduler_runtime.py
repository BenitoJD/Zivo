import logging
import os
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.eta.schedules  # noqa: F401
from app.db import SessionLocal
from app.eta.scheduler_registry import (
    EtaSchedulerDefinition,
    list_scheduler_definitions,
)
from app.models.eta_schedule import EtaSchedule

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReservedEtaSchedule:
    schedule_id: object
    schedule_key: str
    definition: EtaSchedulerDefinition


def _reconcile_schedule_rows(
    *,
    definitions: Sequence[EtaSchedulerDefinition],
    existing_rows: Iterable[EtaSchedule],
    now: datetime,
) -> list[EtaSchedule]:
    existing_by_key = {row.schedule_key: row for row in existing_rows}
    active_keys = set()

    for definition in definitions:
        active_keys.add(definition.key)
        row = existing_by_key.get(definition.key)
        if row is None:
            existing_by_key[definition.key] = EtaSchedule(
                schedule_key=definition.key,
                enabled=True,
                is_orphaned=False,
                next_run_at=definition.next_run_after(now),
                last_error=None,
            )
            continue

        was_orphaned = bool(row.is_orphaned)
        row.is_orphaned = False
        if row.next_run_at is None or was_orphaned:
            row.next_run_at = definition.next_run_after(now)
        if was_orphaned:
            row.enabled = True

    for row in existing_by_key.values():
        if row.schedule_key not in active_keys:
            row.enabled = False
            row.is_orphaned = True

    return list(existing_by_key.values())


def reconcile_scheduler_definitions(db: Session, *, now: datetime | None = None) -> None:
    resolved_now = now or datetime.now(timezone.utc)
    definitions = list(list_scheduler_definitions().values())
    existing_rows = db.query(EtaSchedule).all()
    reconciled_rows = _reconcile_schedule_rows(
        definitions=definitions,
        existing_rows=existing_rows,
        now=resolved_now,
    )
    existing_ids = {row.id for row in existing_rows if row.id is not None}

    for row in reconciled_rows:
        if row.id not in existing_ids:
            row.created_at = resolved_now
            db.add(row)
        row.updated_at = resolved_now

    db.commit()


def _reserve_due_schedules(db: Session, *, now: datetime) -> tuple[list[ReservedEtaSchedule], int]:
    definitions_by_key = list_scheduler_definitions()
    due_schedules = (
        db.execute(
            select(EtaSchedule)
            .where(
                EtaSchedule.enabled,
                EtaSchedule.is_orphaned.isnot(True),
                EtaSchedule.next_run_at <= now,
            )
            .order_by(EtaSchedule.next_run_at.asc())
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )

    reserved: list[ReservedEtaSchedule] = []
    for schedule in due_schedules:
        definition = definitions_by_key.get(schedule.schedule_key)
        if definition is None:
            schedule.enabled = False
            schedule.is_orphaned = True
            schedule.last_error = "Scheduler definition no longer exists"
            schedule.updated_at = now
            continue

        schedule.next_run_at = definition.next_run_after(now)
        schedule.updated_at = now
        reserved.append(
            ReservedEtaSchedule(
                schedule_id=schedule.id,
                schedule_key=schedule.schedule_key,
                definition=definition,
            )
        )

    if due_schedules:
        db.commit()

    return reserved, len(due_schedules)


def _record_schedule_result(
    db: Session,
    *,
    reserved_schedule: ReservedEtaSchedule,
    now: datetime,
    error: str | None,
) -> None:
    schedule = db.get(EtaSchedule, reserved_schedule.schedule_id)
    if schedule is None:
        return

    if error is None:
        schedule.last_run_at = now
        schedule.last_error = None
    else:
        schedule.last_error = error
    schedule.updated_at = now
    db.commit()


def run_due_schedules(db: Session, *, now: datetime | None = None) -> int:
    resolved_now = now or datetime.now(timezone.utc)
    reserved_schedules, claimed_count = _reserve_due_schedules(db, now=resolved_now)

    for reserved_schedule in reserved_schedules:
        try:
            reserved_schedule.definition.fn()
            _record_schedule_result(
                db,
                reserved_schedule=reserved_schedule,
                now=resolved_now,
                error=None,
            )
        except Exception as exc:
            logger.exception(
                "ETA scheduler execution failed",
                extra={"schedule_key": reserved_schedule.schedule_key},
            )
            _record_schedule_result(
                db,
                reserved_schedule=reserved_schedule,
                now=resolved_now,
                error=str(exc),
            )

    return claimed_count


class EtaSchedulerService:
    def __init__(
        self,
        *,
        enabled: bool | None = None,
        poll_interval_seconds: float | None = None,
    ) -> None:
        self.enabled = (
            enabled
            if enabled is not None
            else os.getenv("ETA_SCHEDULER_ENABLED", "true").lower() == "true"
            and os.getenv("ETA_SCHEDULER_PRIMARY", "true").lower() == "true"
        )
        self.poll_interval_seconds = poll_interval_seconds or float(
            os.getenv("ETA_SCHEDULER_POLL_INTERVAL_SECONDS", "5.0")
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled:
            logger.info("ETA scheduler disabled")
            return

        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="eta-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "ETA scheduler started",
            extra={"poll_interval_seconds": self.poll_interval_seconds},
        )

    def stop(self) -> None:
        if not self._thread:
            return

        self._stop_event.set()
        self._thread.join(timeout=self.poll_interval_seconds + 1)
        self._thread = None
        logger.info("ETA scheduler stopped")

    def _run_loop(self) -> None:
        try:
            with SessionLocal() as db:
                reconcile_scheduler_definitions(db)
        except Exception:
            logger.exception("ETA scheduler reconcile failed during startup")

        cache_cleanup_counter = 0
        job_cleanup_counter = 0
        while not self._stop_event.is_set():
            triggered = 0
            try:
                with SessionLocal() as db:
                    triggered = run_due_schedules(db)
                    cache_cleanup_counter += 1
                    job_cleanup_counter += 1
                    if cache_cleanup_counter >= 720:  # ~1h at 5s poll
                        from app.services.response_cache import cleanup_stale_cache

                        removed = cleanup_stale_cache(db, max_age_days=30)
                        cache_cleanup_counter = 0
                        if removed:
                            logger.info("LLM response cache cleanup", extra={"removed": removed})
                    if job_cleanup_counter >= 720:
                        from app.services.job_retention import cleanup_terminal_jobs

                        jobs_removed = cleanup_terminal_jobs(db, max_age_days=14)
                        job_cleanup_counter = 0
                        if jobs_removed:
                            logger.info("ETA job retention cleanup", extra={"removed": jobs_removed})
                if triggered:
                    logger.info("ETA scheduler dispatched runs", extra={"count": triggered})
            except Exception:
                logger.exception("ETA scheduler loop failed")

            self._stop_event.wait(self.poll_interval_seconds)


eta_scheduler_service = EtaSchedulerService()
