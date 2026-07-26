"""Per-learner learn state for shared newspaper editions.

Revision ID: 034_document_learner_state
Revises: 033_rate_limit_hits
Create Date: 2026-07-26

Newspaper MCQs are cooked once per edition but answered independently by each
learner (account or guest). ``document_learn_state`` stays document-wide for
cook/index metadata; ``document_learner_state`` holds answered_ids and pacing.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "034_document_learner_state"
down_revision: Union[str, None] = "033_rate_limit_hits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.document_learner_state (
          document_id   UUID NOT NULL REFERENCES qb.documents (id) ON DELETE CASCADE,
          learner_key   TEXT NOT NULL,
          progress      JSONB NOT NULL DEFAULT '{}',
          updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (document_id, learner_key)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_learner_state_learner_key
        ON qb.document_learner_state (learner_key)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qb.document_learner_state")
