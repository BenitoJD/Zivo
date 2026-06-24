"""Retention for terminal ETA jobs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session


def cleanup_terminal_jobs(db: Session, *, max_age_days: int = 14) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    result = db.execute(
        text(
            """
            DELETE FROM qb.jobs
            WHERE status IN ('succeeded', 'failed', 'cancelled')
              AND finished_at IS NOT NULL
              AND finished_at < :cutoff
            """
        ),
        {"cutoff": cutoff},
    )
    db.commit()
    return result.rowcount or 0
