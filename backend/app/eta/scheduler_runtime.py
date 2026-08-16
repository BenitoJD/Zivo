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
from app.engine_runtime import Pred, Rule, apply, first_match, pick
from app.eta.scheduler_registry import (
    EtaSchedulerDefinition,
    list_scheduler_definitions,
)
from app.models.eta_schedule import EtaSchedule

logger = logging.getLogger(__name__)

_CACHE_CLEANUP_TICKS = 720
_JOB_CLEANUP_TICKS = 720

_RECORD_RULES = (
    Rule(when=(Pred("ok", "truthy"),), action="success"),
    Rule(when=(), action="error"),
)


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

    def _upsert(definition: EtaSchedulerDefinition) -> None:
        active_keys.add(definition.key)
        row = existing_by_key.get(definition.key)

        def _create() -> None:
            existing_by_key[definition.key] = EtaSchedule(
                schedule_key=definition.key,
                enabled=True,
                is_orphaned=False,
                next_run_at=definition.next_run_after(now),
                last_error=None,
            )

        def _refresh() -> None:
            was_orphaned = bool(row.is_orphaned)
            row.is_orphaned = False
            pick(
                row.next_run_at is None or was_orphaned,
                lambda: setattr(row, "next_run_at", definition.next_run_after(now)),
                lambda: None,
            )
            pick(was_orphaned, lambda: setattr(row, "enabled", True), lambda: None)

        pick(row is None, _create, _refresh)

    for definition in definitions:
        _upsert(definition)

    def _orphan(row: EtaSchedule) -> None:
        def _mark() -> None:
            row.enabled = False
            row.is_orphaned = True

        pick(row.schedule_key not in active_keys, _mark, lambda: None)

    for row in existing_by_key.values():
        _orphan(row)

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
    existing_ids = set(
        map(
            lambda row: row.id,
            filter(lambda row: row.id is not None, existing_rows),
        )
    )

    def _persist(row: EtaSchedule) -> None:
        def _add() -> None:
            row.created_at = resolved_now
            db.add(row)

        pick(row.id not in existing_ids, _add, lambda: None)
        row.updated_at = resolved_now

    for row in reconciled_rows:
        _persist(row)

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

    def _claim(schedule: EtaSchedule) -> ReservedEtaSchedule | None:
        definition = definitions_by_key.get(schedule.schedule_key)

        def _missing() -> None:
            schedule.enabled = False
            schedule.is_orphaned = True
            schedule.last_error = "Scheduler definition no longer exists"
            schedule.updated_at = now

        def _reserve() -> ReservedEtaSchedule:
            schedule.next_run_at = definition.next_run_after(now)
            schedule.updated_at = now
            return ReservedEtaSchedule(
                schedule_id=schedule.id,
                schedule_key=schedule.schedule_key,
                definition=definition,
            )

        pick(definition is None, _missing, lambda: None)
        return pick(definition is None, lambda: None, _reserve)

    reserved = list(filter(None, map(_claim, due_schedules)))
    pick(bool(due_schedules), db.commit, lambda: None)
    return reserved, len(due_schedules)


def _record_schedule_result(
    db: Session,
    *,
    reserved_schedule: ReservedEtaSchedule,
    now: datetime,
    error: str | None,
) -> None:
    schedule = db.get(EtaSchedule, reserved_schedule.schedule_id)

    def _write(active: EtaSchedule) -> None:
        hit = first_match(_RECORD_RULES, {"ok": error is None})
        apply(
            hit.action,
            {
                "success": lambda: (
                    setattr(active, "last_run_at", now),
                    setattr(active, "last_error", None),
                ),
                "error": lambda: setattr(active, "last_error", error),
            },
        )
        active.updated_at = now
        db.commit()

    pick(schedule is None, lambda: None, lambda: _write(schedule))


def run_due_schedules(db: Session, *, now: datetime | None = None) -> int:
    resolved_now = now or datetime.now(timezone.utc)
    reserved_schedules, claimed_count = _reserve_due_schedules(db, now=resolved_now)

    def _run_one(reserved_schedule: ReservedEtaSchedule) -> None:
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

    for reserved_schedule in reserved_schedules:
        _run_one(reserved_schedule)

    return claimed_count


class EtaSchedulerService:
    def __init__(
        self,
        *,
        enabled: bool | None = None,
        poll_interval_seconds: float | None = None,
    ) -> None:
        self.enabled = pick(
            enabled is not None,
            lambda: enabled,
            lambda: os.getenv("ETA_SCHEDULER_ENABLED", "true").lower() == "true"
            and os.getenv("ETA_SCHEDULER_PRIMARY", "true").lower() == "true",
        )
        self.poll_interval_seconds = poll_interval_seconds or float(
            os.getenv("ETA_SCHEDULER_POLL_INTERVAL_SECONDS", "5.0")
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _spawn(self) -> None:
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

    def start(self) -> None:
        def _maybe_spawn() -> None:
            pick(bool(self._thread and self._thread.is_alive()), lambda: None, self._spawn)

        pick(not self.enabled, lambda: logger.info("ETA scheduler disabled"), _maybe_spawn)

    def stop(self) -> None:
        def _stop() -> None:
            self._stop_event.set()
            self._thread.join(timeout=self.poll_interval_seconds + 1)
            self._thread = None
            logger.info("ETA scheduler stopped")

        pick(not self._thread, lambda: None, _stop)

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

                    def _cache_cleanup() -> None:
                        nonlocal cache_cleanup_counter
                        from app.services.response_cache import cleanup_stale_cache

                        removed = cleanup_stale_cache(db, max_age_days=30)
                        cache_cleanup_counter = 0
                        pick(
                            bool(removed),
                            lambda: logger.info("LLM response cache cleanup", extra={"removed": removed}),
                            lambda: None,
                        )

                    def _job_cleanup() -> None:
                        nonlocal job_cleanup_counter
                        from app.services.job_retention import cleanup_terminal_jobs

                        jobs_removed = cleanup_terminal_jobs(db, max_age_days=14)
                        job_cleanup_counter = 0
                        pick(
                            bool(jobs_removed),
                            lambda: logger.info(
                                "ETA job retention cleanup", extra={"removed": jobs_removed}
                            ),
                            lambda: None,
                        )

                    pick(cache_cleanup_counter >= _CACHE_CLEANUP_TICKS, _cache_cleanup, lambda: None)
                    pick(job_cleanup_counter >= _JOB_CLEANUP_TICKS, _job_cleanup, lambda: None)
                pick(
                    bool(triggered),
                    lambda: logger.info("ETA scheduler dispatched runs", extra={"count": triggered}),
                    lambda: None,
                )
            except Exception:
                logger.exception("ETA scheduler loop failed")

            self._stop_event.wait(self.poll_interval_seconds)


eta_scheduler_service = EtaSchedulerService()
