"""Saved notes for the Read / Study-Buddy mode.

Revision ID: 017_saved_notes
Revises: 016_memory_palace
Create Date: 2026-06-27

While reading a source with the study buddy, a learner can save useful answers (and the
passage they quoted) as notes that stay linked to the document. A simple append-only
list per document; cascade-deletes with the document.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "017_saved_notes"
down_revision: Union[str, None] = "016_memory_palace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.document_saved_notes (
          id          UUID PRIMARY KEY,
          document_id UUID NOT NULL REFERENCES qb.documents(id) ON DELETE CASCADE,
          content     TEXT NOT NULL,
          quote       TEXT,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_document_saved_notes_doc "
        "ON qb.document_saved_notes (document_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qb.document_saved_notes")
