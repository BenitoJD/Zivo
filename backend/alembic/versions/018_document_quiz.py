"""Question Generator (Quiz Builder) — educator worksheet/quiz from any document.

Revision ID: 018_document_quiz
Revises: 017_saved_notes
Create Date: 2026-06-27

One generated quiz per document (regenerated when the requested config — question
types / count / difficulty — changes). Stores the structured question set with an
answer key so it can be previewed (teacher vs student view) and exported to
PDF / DOCX / Markdown / plain text. Cascade-deletes with the document.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "018_document_quiz"
down_revision: Union[str, None] = "017_saved_notes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.document_quiz (
          document_id UUID PRIMARY KEY REFERENCES qb.documents(id) ON DELETE CASCADE,
          config      TEXT NOT NULL DEFAULT '',
          questions   JSONB NOT NULL DEFAULT '[]'::jsonb,
          status      TEXT NOT NULL DEFAULT 'pending',
          error       TEXT,
          updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qb.document_quiz")
