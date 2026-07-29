"""Per-learner interview state for shared resume documents.

Revision ID: 041_interview_learner_scope
Revises: 040_guest_note_scope
"""

from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "041_interview_learner_scope"
down_revision: Union[str, None] = "040_guest_note_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE qb.document_interview
        ADD COLUMN IF NOT EXISTS learner_key TEXT NOT NULL DEFAULT 'legacy'
        """
    )
    op.execute(
        """
        ALTER TABLE qb.document_interview
        DROP CONSTRAINT IF EXISTS document_interview_pkey
        """
    )
    op.execute(
        """
        ALTER TABLE qb.document_interview
        ADD PRIMARY KEY (document_id, learner_key)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_interview_learner_key
        ON qb.document_interview (learner_key)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS qb.ix_document_interview_learner_key")
    op.execute(
        """
        DELETE FROM qb.document_interview di
        WHERE di.learner_key <> 'legacy'
          AND EXISTS (
            SELECT 1 FROM qb.document_interview keep
            WHERE keep.document_id = di.document_id
              AND keep.learner_key = 'legacy'
          )
        """
    )
    op.execute(
        """
        ALTER TABLE qb.document_interview
        DROP CONSTRAINT IF EXISTS document_interview_pkey
        """
    )
    op.execute(
        """
        ALTER TABLE qb.document_interview
        ADD PRIMARY KEY (document_id)
        """
    )
    op.execute(
        """
        ALTER TABLE qb.document_interview
        DROP COLUMN IF EXISTS learner_key
        """
    )
