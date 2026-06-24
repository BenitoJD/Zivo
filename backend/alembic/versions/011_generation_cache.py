"""Generation draft cache — survive worker restart, share across workers.

Revision ID: 011_generation_cache
Revises: 010_page_content_hash_index
Create Date: 2026-06-25
"""

from typing import Sequence, Union

from alembic import op

revision: str = "011_generation_cache"
down_revision: Union[str, None] = "010_page_content_hash_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.generation_cache (
          cache_key TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          value JSONB NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # TTL sweep is opportunistic in the service layer; this index keeps the
    # created_at scan cheap once the table grows.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_generation_cache_created_at
        ON qb.generation_cache (created_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qb.generation_cache")
