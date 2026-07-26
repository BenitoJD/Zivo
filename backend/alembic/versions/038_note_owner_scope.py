"""Owner-scope saved notes & brainstorm ideas (IDOR fix).

Revision ID: 038_note_owner_scope
Revises: 037_newspaper_edition_blog
Create Date: 2026-07-26

Both ``qb.document_saved_notes`` and ``qb.document_brainstorm_ideas`` were keyed
only by ``document_id``. ``require_document`` grants read access to every user
on ``is_demo`` / ``is_public`` documents (the shared demo doc, public practice
sources), so any authenticated user could DELETE or overwrite any other user's
notes/ideas on those documents (IDOR).

This adds a nullable ``account_id`` to both tables (additive — existing NULL
rows stay visible to all callers, no data migration required) and an index that
backs the per-owner list/delete filters the service layer now applies.
``ON DELETE SET NULL`` so an account deletion doesn't lose shared notes; the
document FK still cascades the row when its document goes.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "038_note_owner_scope"
down_revision: Union[str, None] = "037_newspaper_edition_blog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE qb.document_saved_notes
          ADD COLUMN IF NOT EXISTS account_id UUID
            REFERENCES qb.account (id) ON DELETE SET NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_saved_notes_owner
          ON qb.document_saved_notes (document_id, account_id)
        """
    )
    op.execute(
        """
        ALTER TABLE qb.document_brainstorm_ideas
          ADD COLUMN IF NOT EXISTS account_id UUID
            REFERENCES qb.account (id) ON DELETE SET NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_brainstorm_ideas_owner
          ON qb.document_brainstorm_ideas (document_id, account_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS qb.ix_document_brainstorm_ideas_owner")
    op.execute("ALTER TABLE qb.document_brainstorm_ideas DROP COLUMN IF EXISTS account_id")
    op.execute("DROP INDEX IF EXISTS qb.ix_document_saved_notes_owner")
    op.execute("ALTER TABLE qb.document_saved_notes DROP COLUMN IF EXISTS account_id")
