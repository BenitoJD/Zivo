"""Resume suite — ATS score/checklist + structured resume, cached per document.

Revision ID: 021_document_resume
Revises: 020_document_interview
Create Date: 2026-07-02

One row per resume document caching the ATS analysis (deterministic checks + an LLM content
review) and the structured resume extracted from it (so the builder can pre-fill). The
optimizer is job-description-specific and computed live (not cached). Cascade-deletes with
the document.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "021_document_resume"
down_revision: Union[str, None] = "020_document_interview"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.document_resume (
          document_id      UUID PRIMARY KEY REFERENCES qb.documents(id) ON DELETE CASCADE,
          analysis         JSONB NOT NULL DEFAULT '{}'::jsonb,
          status           TEXT NOT NULL DEFAULT 'pending',
          error            TEXT,
          review_requested BOOLEAN NOT NULL DEFAULT false,
          updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qb.document_resume")
