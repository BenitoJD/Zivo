"""Lease/heartbeat crash recovery for qb.jobs.

Adds heartbeat_at (renewed by the worker while a job runs) and lease_deadline
(the instant after which the reaper may treat the job as orphaned). Together
they replace the old fixed 600s locked_at guess that false-reclaimed long jobs.

Additive: both columns default NULL, so running pods that pre-date this migration
keep working — the reaper falls back to locked_at when lease_deadline is NULL.

Revision ID: 042_job_lease_heartbeat
Revises: 041_interview_learner_scope
"""

from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "042_job_lease_heartbeat"
down_revision: Union[str, None] = "041_interview_learner_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE qb.jobs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ")
    op.execute("ALTER TABLE qb.jobs ADD COLUMN IF NOT EXISTS lease_deadline TIMESTAMPTZ")
    # Partial index: the reaper scans only 'running' rows past their lease deadline.
    # Small (only in-flight jobs) and exactly the set the reaper queries.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_jobs_running_lease_deadline
        ON qb.jobs (lease_deadline)
        WHERE status = 'running'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS qb.ix_jobs_running_lease_deadline")
    op.execute("ALTER TABLE qb.jobs DROP COLUMN IF EXISTS lease_deadline")
    op.execute("ALTER TABLE qb.jobs DROP COLUMN IF EXISTS heartbeat_at")
