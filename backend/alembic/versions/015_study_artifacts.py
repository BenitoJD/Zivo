"""Scribely-style study artifacts: notes, cheat sheets, flashcards (+ async explain).

Revision ID: 015_study_artifacts
Revises: 014_learn_topics
Create Date: 2026-06-27

Additive to MCQ/Explain. Three new study outputs per document, each generated off the
answer path by a worker and cached:

  * ``qb.document_notes``      — structured markdown study notes; ``kind`` discriminates
                                 full "notes" from a condensed "cheatsheet".
  * ``qb.document_flashcards`` — active-recall cards (Q/A + cloze) as a JSON array.

Also makes per-topic explanations async like the outline: ``qb.topic_explanation`` gains
a ``status``/``error`` and ``explanation`` becomes nullable so a row can exist in the
"generating" state before the text is ready. All cascade-delete with the document.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "015_study_artifacts"
down_revision: Union[str, None] = "014_learn_topics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.document_notes (
          document_id UUID NOT NULL REFERENCES qb.documents(id) ON DELETE CASCADE,
          kind        TEXT NOT NULL DEFAULT 'notes',
          content     TEXT NOT NULL DEFAULT '',
          status      TEXT NOT NULL DEFAULT 'pending',
          error       TEXT,
          updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (document_id, kind)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.document_flashcards (
          document_id UUID PRIMARY KEY REFERENCES qb.documents(id) ON DELETE CASCADE,
          cards       JSONB NOT NULL DEFAULT '[]'::jsonb,
          status      TEXT NOT NULL DEFAULT 'pending',
          error       TEXT,
          updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Make per-topic explanations async-capable (mirror document_topics).
    op.execute("ALTER TABLE qb.topic_explanation ALTER COLUMN explanation DROP NOT NULL")
    op.execute(
        "ALTER TABLE qb.topic_explanation ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'ready'"
    )
    op.execute("ALTER TABLE qb.topic_explanation ADD COLUMN IF NOT EXISTS error TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE qb.topic_explanation DROP COLUMN IF EXISTS error")
    op.execute("ALTER TABLE qb.topic_explanation DROP COLUMN IF EXISTS status")
    op.execute("DROP TABLE IF EXISTS qb.document_flashcards")
    op.execute("DROP TABLE IF EXISTS qb.document_notes")
