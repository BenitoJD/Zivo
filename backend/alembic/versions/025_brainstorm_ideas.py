"""Brainstorm mode — saved ideas (idea board + mind map are one tree).

Revision ID: 025_brainstorm_ideas
Revises: 024_llm_vision_only
Create Date: 2026-07-22

Additive. Brainstorm itself is a chat surface (no generated artifact, no worker);
this table holds only the ideas the learner explicitly keeps. ``parent_id`` is what
makes the board and the mind map the same data: a flat ordered read renders the
board, the same rows nested by parent render the map. Self-referential cascade so
deleting a branch deletes what grew out of it. Cascade-deletes with the document.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "025_brainstorm_ideas"
down_revision: Union[str, None] = "024_llm_vision_only"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.document_brainstorm_ideas (
          id          UUID PRIMARY KEY,
          document_id UUID NOT NULL REFERENCES qb.documents(id) ON DELETE CASCADE,
          parent_id   UUID REFERENCES qb.document_brainstorm_ideas(id) ON DELETE CASCADE,
          text        TEXT NOT NULL,
          angle       TEXT NOT NULL DEFAULT '',
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_brainstorm_ideas_doc "
        "ON qb.document_brainstorm_ideas (document_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qb.document_brainstorm_ideas")
