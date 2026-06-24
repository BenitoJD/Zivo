"""LLM usage event ledger for cost attribution.

Revision ID: 008_llm_usage_events
Revises: 007_embedding_hnsw_upload_sessions
Create Date: 2026-06-24
"""

from typing import Sequence, Union

from alembic import op

revision: str = "008_llm_usage_events"
down_revision: Union[str, None] = "007_embedding_hnsw_upload_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.llm_usage_event (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tag VARCHAR(64) NOT NULL,
          model VARCHAR(256) NOT NULL,
          prompt_tokens INT NOT NULL DEFAULT 0,
          completion_tokens INT NOT NULL DEFAULT 0,
          cached_tokens INT NOT NULL DEFAULT 0,
          latency_ms INT NOT NULL DEFAULT 0,
          account_id UUID REFERENCES qb.account (id) ON DELETE SET NULL,
          document_id UUID,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS llm_usage_event_created_at_idx
          ON qb.llm_usage_event (created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS llm_usage_event_document_idx
          ON qb.llm_usage_event (document_id, created_at DESC)
          WHERE document_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qb.llm_usage_event")
