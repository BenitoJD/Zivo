"""Memory Palace mode (Anthony Metivier's Magnetic Memory Method).

Revision ID: 016_memory_palace
Revises: 015_study_artifacts
Create Date: 2026-06-27

Additive to MCQ/Explain/Notes. One memory palace per document: an AI-built journey of
"stations" through a familiar place, each anchoring a key fact with a vivid multisensory
(KAVE COGS) mnemonic image and a recall cue. Generated off the answer path by a worker
and cached; ``setting`` records the place used so re-personalizing can regenerate it.
Cascade-deletes with the document.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "016_memory_palace"
down_revision: Union[str, None] = "015_study_artifacts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.document_memory_palace (
          document_id UUID PRIMARY KEY REFERENCES qb.documents(id) ON DELETE CASCADE,
          setting     TEXT NOT NULL DEFAULT '',
          palace      JSONB NOT NULL DEFAULT '{}'::jsonb,
          status      TEXT NOT NULL DEFAULT 'pending',
          error       TEXT,
          updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qb.document_memory_palace")
