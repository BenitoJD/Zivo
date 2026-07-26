"""Per-page Learn lessons — AI-generated teaching prose shown before a page's MCQs.

Revision ID: 039_page_lessons
Revises: 038_note_owner_scope
Create Date: 2026-07-26

In Learn mode, before a page's questions are served, the learner reads a short,
question-aware lesson that teaches the concepts that page's MCQs will test.
One row per (document, page), generated during the page cook, before the MCQ
loop. Follows the ``qb.document_*`` artifact lifecycle (pending -> generating ->
ready | failed) via :class:`ArtifactStore`, with a ``content_hash`` for per-page
staleness (mirrors the MCQ ``page_content_hash`` regeneration signal).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "039_page_lessons"
down_revision: Union[str, None] = "038_note_owner_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.page_lessons (
          document_id   UUID NOT NULL REFERENCES qb.documents (id) ON DELETE CASCADE,
          page_number   INT  NOT NULL,
          lesson        JSONB NOT NULL DEFAULT '{}'::jsonb,
          content_hash  TEXT NOT NULL DEFAULT '',
          status        TEXT NOT NULL DEFAULT 'pending',
          error         TEXT,
          updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (document_id, page_number)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qb.page_lessons")
