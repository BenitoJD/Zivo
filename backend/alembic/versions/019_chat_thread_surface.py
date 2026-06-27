"""Per-mode chat threads — add a `surface` to chat_thread.

Revision ID: 019_chat_thread_surface
Revises: 018_document_quiz
Create Date: 2026-06-28

The tutor chat was one thread per document, shared across study modes. Add a
`surface` column ("read" / "learn" / "test", else "general") and fold it into
the per-version unique key so each mode gets its own independent conversation.
Existing threads default to "general" (orphaned from the new per-mode views,
not deleted).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "019_chat_thread_surface"
down_revision: Union[str, None] = "018_document_quiz"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF EXISTS: chat_thread is created by create_all, so it may not exist yet on a
    # brand-new DB at migrate time — then create_all makes it correctly from the model.
    op.execute(
        "ALTER TABLE IF EXISTS qb.chat_thread ADD COLUMN IF NOT EXISTS surface TEXT NOT NULL DEFAULT 'general'"
    )
    op.execute("ALTER TABLE IF EXISTS qb.chat_thread DROP CONSTRAINT IF EXISTS uq_chat_thread_version")
    op.execute(
        """
        ALTER TABLE IF EXISTS qb.chat_thread
        ADD CONSTRAINT uq_chat_thread_version
        UNIQUE (account_id, artifact_id, artifact_captured_at, surface, version)
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS qb.chat_thread DROP CONSTRAINT IF EXISTS uq_chat_thread_version")
    op.execute(
        """
        ALTER TABLE IF EXISTS qb.chat_thread
        ADD CONSTRAINT uq_chat_thread_version
        UNIQUE (account_id, artifact_id, artifact_captured_at, version)
        """
    )
    op.execute("ALTER TABLE IF EXISTS qb.chat_thread DROP COLUMN IF EXISTS surface")
