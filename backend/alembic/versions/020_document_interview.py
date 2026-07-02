"""Interview Mode — multi-round mock interview from an uploaded resume.

Revision ID: 020_document_interview
Revises: 019_chat_thread_surface
Create Date: 2026-07-02

One active interview per document (the resume). Unlike the cached study artifacts,
this single row is MUTATED per turn: the learner picks a target company category, the
app resolves a round plan, then asks one question at a time (MCQ or typed), evaluating
each answer and appending it to the transcript until every round is complete. Raw
parameterized SQL on qb.document_interview. Cascade-deletes with the document.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "020_document_interview"
down_revision: Union[str, None] = "019_chat_thread_surface"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.document_interview (
          document_id UUID PRIMARY KEY REFERENCES qb.documents(id) ON DELETE CASCADE,
          category    TEXT NOT NULL DEFAULT '',
          config      JSONB NOT NULL DEFAULT '[]'::jsonb,
          state       JSONB NOT NULL DEFAULT '{}'::jsonb,
          transcript  JSONB NOT NULL DEFAULT '[]'::jsonb,
          status      TEXT NOT NULL DEFAULT 'setup',
          error       TEXT,
          updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qb.document_interview")
