"""Explain feature: per-document topic outline + cached plain-language explanations.

Revision ID: 014_learn_topics
Revises: 013_measurement_answer_idempotent
Create Date: 2026-06-27

Backs the new "Explain" mode (additive to MCQ): one topic outline per document, and a
cache of per-topic high-level explanations so re-opening a topic is instant. Both
cascade-delete with the document.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "014_learn_topics"
down_revision: Union[str, None] = "013_measurement_answer_idempotent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.document_topics (
          document_id UUID PRIMARY KEY REFERENCES qb.documents(id) ON DELETE CASCADE,
          outline     JSONB NOT NULL DEFAULT '[]'::jsonb,
          status      TEXT NOT NULL DEFAULT 'pending',
          error       TEXT,
          updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.topic_explanation (
          document_id UUID NOT NULL REFERENCES qb.documents(id) ON DELETE CASCADE,
          topic_key   TEXT NOT NULL,
          explanation TEXT NOT NULL,
          updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (document_id, topic_key)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qb.topic_explanation")
    op.execute("DROP TABLE IF EXISTS qb.document_topics")
