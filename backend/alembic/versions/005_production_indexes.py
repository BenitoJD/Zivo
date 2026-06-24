"""Production indexes for queue pick, assertions, chat, guest docs.

Revision ID: 005_production_indexes
Revises: 004_assertion_page_index
Create Date: 2026-06-24
"""

from typing import Sequence, Union

from alembic import op

revision: str = "005_production_indexes"
down_revision: Union[str, None] = "004_assertion_page_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_jobs_queued_pick
        ON qb.jobs (workload, priority DESC, created_at)
        WHERE status = 'queued'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_assertion_payload_artifact_id
        ON intel.assertion ((payload->>'artifact_id'))
        WHERE status = 'active'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_chat_thread_account_id
        ON qb.chat_thread (account_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_documents_guest_id
        ON qb.documents ((meta->>'guest_id'))
        WHERE account_id IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS qb.ix_documents_guest_id")
    op.execute("DROP INDEX IF EXISTS qb.ix_chat_thread_account_id")
    op.execute("DROP INDEX IF EXISTS intel.ix_assertion_payload_artifact_id")
    op.execute("DROP INDEX IF EXISTS qb.ix_jobs_queued_pick")
