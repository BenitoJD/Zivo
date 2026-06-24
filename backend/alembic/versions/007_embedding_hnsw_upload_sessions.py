"""Fixed-dim intel.embedding HNSW + chunked upload sessions.

Revision ID: 007_embedding_hnsw_upload_sessions
Revises: 006_production_schema
Create Date: 2026-06-24
"""

from typing import Sequence, Union

from alembic import op

revision: str = "007_embedding_hnsw_upload_sessions"
down_revision: Union[str, None] = "006_production_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE intel.embedding
        ALTER COLUMN embedding TYPE vector(384)
        USING CASE
          WHEN embedding IS NULL THEN NULL
          ELSE embedding::vector(384)
        END
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS embedding_hnsw_idx
        ON intel.embedding USING hnsw (embedding vector_cosine_ops)
        WHERE dimensions = 384
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.upload_session (
          id UUID PRIMARY KEY,
          account_id UUID REFERENCES qb.account (id) ON DELETE CASCADE,
          guest_id TEXT,
          filename TEXT NOT NULL,
          content_type TEXT NOT NULL,
          total_size BIGINT NOT NULL,
          chunk_size INTEGER NOT NULL,
          storage_key TEXT NOT NULL,
          multipart_upload_id TEXT NOT NULL,
          parts JSONB NOT NULL DEFAULT '{}',
          expires_at TIMESTAMPTZ NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_upload_session_expires
        ON qb.upload_session (expires_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS qb.ix_upload_session_expires")
    op.execute("DROP TABLE IF EXISTS qb.upload_session")
    op.execute("DROP INDEX IF EXISTS intel.embedding_hnsw_idx")
    op.execute(
        """
        ALTER TABLE intel.embedding
        ALTER COLUMN embedding TYPE vector
        USING embedding::vector
        """
    )
