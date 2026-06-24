"""Assertion page lookup index for learn-queue hot path.

Revision ID: 004_assertion_page_index
Revises: 003_disable_mimo_model
Create Date: 2026-06-24
"""

from typing import Sequence, Union

from alembic import op

revision: str = "004_assertion_page_index"
down_revision: Union[str, None] = "003_disable_mimo_model"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_assertion_artifact_page_active
        ON intel.assertion ((payload->>'artifact_id'), ((payload->>'page_number')::int))
        WHERE status = 'active'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS intel.ix_assertion_artifact_page_active")
