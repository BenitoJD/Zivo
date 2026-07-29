"""Scope guest saved notes and brainstorm ideas by guest_id.

Revision ID: 040_guest_note_scope
Revises: 039_page_lessons
"""

from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "040_guest_note_scope"
down_revision: Union[str, None] = "039_page_lessons"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE qb.document_saved_notes
        ADD COLUMN IF NOT EXISTS guest_id TEXT
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_saved_notes_guest
        ON qb.document_saved_notes (document_id, guest_id)
        WHERE guest_id IS NOT NULL
        """
    )
    op.execute(
        """
        ALTER TABLE qb.document_brainstorm_ideas
        ADD COLUMN IF NOT EXISTS guest_id TEXT
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_brainstorm_ideas_guest
        ON qb.document_brainstorm_ideas (document_id, guest_id)
        WHERE guest_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS qb.ix_document_brainstorm_ideas_guest")
    op.execute("ALTER TABLE qb.document_brainstorm_ideas DROP COLUMN IF EXISTS guest_id")
    op.execute("DROP INDEX IF EXISTS qb.ix_document_saved_notes_guest")
    op.execute("ALTER TABLE qb.document_saved_notes DROP COLUMN IF EXISTS guest_id")
