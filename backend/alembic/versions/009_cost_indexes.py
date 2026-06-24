"""Cost-optimization indexes — jobs payload + assertion artifact lookups.

Revision ID: 009_cost_indexes
Revises: 008_llm_usage_events
Create Date: 2026-06-24
"""

from typing import Sequence, Union

from alembic import op

revision: str = "009_cost_indexes"
down_revision: Union[str, None] = "008_llm_usage_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE qb.jobs
        ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_jobs_payload_document_id
        ON qb.jobs ((payload->>'document_id'))
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_assertion_payload_artifact_id_all
        ON intel.assertion ((payload->>'artifact_id'))
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS intel.ix_assertion_payload_artifact_id_all")
    op.execute("DROP INDEX IF EXISTS qb.ix_jobs_payload_document_id")
