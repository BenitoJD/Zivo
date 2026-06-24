"""Shared MCQ reuse index on page content hash.

Revision ID: 010_page_content_hash_index
Revises: 009_cost_indexes
Create Date: 2026-06-24
"""

from typing import Sequence, Union

from alembic import op

revision: str = "010_page_content_hash_index"
down_revision: Union[str, None] = "009_cost_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_assertion_page_content_hash
        ON intel.assertion ((payload->>'page_content_hash'), (payload->>'page_number'))
        WHERE status = 'active'
          AND payload->>'page_content_hash' IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS intel.ix_assertion_page_content_hash")
