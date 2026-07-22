"""Mains mode — examiner-style grading of a long descriptive answer.

Revision ID: 023_document_mains
Revises: 022_coding_assertion_facets
Create Date: 2026-07-22

One in-flight Mains attempt per document: a Zivo-generated descriptive question +
a hidden marking scheme, the candidate's answer (typed, or the text read out of an
uploaded handwritten photo by the vision LLM), and the graded result (marks +
per-axis scores + examiner comments). `status` drives the async pipeline:
generating -> awaiting_answer -> grading -> ready (or failed). Cascade-deletes
with the document. `scheme` is never sent to the client until grading reveals it.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "023_document_mains"
down_revision: Union[str, None] = "022_coding_assertion_facets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.document_mains (
          document_id UUID PRIMARY KEY REFERENCES qb.documents(id) ON DELETE CASCADE,
          question    TEXT  NOT NULL DEFAULT '',
          scheme      JSONB NOT NULL DEFAULT '{}'::jsonb,
          answer      TEXT  NOT NULL DEFAULT '',
          result      JSONB NOT NULL DEFAULT '{}'::jsonb,
          config      JSONB NOT NULL DEFAULT '{}'::jsonb,
          status      TEXT  NOT NULL DEFAULT 'pending',
          error       TEXT,
          updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qb.document_mains")
