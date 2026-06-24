"""Learn state table, MCQ facet index, session version, drop legacy session.

Revision ID: 006_production_schema
Revises: 005_production_indexes
Create Date: 2026-06-24
"""

from typing import Sequence, Union

from alembic import op

revision: str = "006_production_schema"
down_revision: Union[str, None] = "005_production_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.document_learn_state (
          document_id UUID PRIMARY KEY REFERENCES qb.documents(id) ON DELETE CASCADE,
          progress JSONB NOT NULL DEFAULT '{}',
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        INSERT INTO qb.document_learn_state (document_id, progress)
        SELECT id, COALESCE(meta->'question_progress', '{}'::jsonb)
        FROM qb.documents
        WHERE meta ? 'question_progress'
        ON CONFLICT (document_id) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.mcq_assertion_facets (
          assertion_id UUID PRIMARY KEY,
          artifact_id UUID NOT NULL,
          page_number INTEGER NOT NULL,
          sequence INTEGER NOT NULL,
          question TEXT,
          correct_index INTEGER,
          CONSTRAINT mcq_assertion_facets_assertion_fk
            FOREIGN KEY (assertion_id) REFERENCES intel.assertion(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_mcq_facets_artifact_page_seq
        ON qb.mcq_assertion_facets (artifact_id, page_number, sequence)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_mcq_facets_artifact_page
        ON qb.mcq_assertion_facets (artifact_id, page_number)
        """
    )
    op.execute(
        """
        INSERT INTO qb.mcq_assertion_facets (
          assertion_id, artifact_id, page_number, sequence, question, correct_index
        )
        SELECT
          id,
          (payload->>'artifact_id')::uuid,
          (payload->>'page_number')::int,
          (payload->>'sequence')::int,
          COALESCE(payload->>'question', payload->>'stem'),
          (payload->>'correct_index')::int
        FROM intel.assertion
        WHERE status = 'active'
          AND payload->>'artifact_id' IS NOT NULL
          AND payload->>'page_number' IS NOT NULL
          AND payload->>'sequence' IS NOT NULL
        ON CONFLICT (assertion_id) DO NOTHING
        """
    )
    op.execute(
        """
        ALTER TABLE qb.account
        ADD COLUMN IF NOT EXISTS session_version INTEGER NOT NULL DEFAULT 0
        """
    )
    op.execute("DROP TABLE IF EXISTS qb.session")
    op.execute(
        """
        ALTER TABLE qb.jobs
        ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_jobs_terminal_finished
        ON qb.jobs (finished_at)
        WHERE status IN ('succeeded', 'failed', 'cancelled')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS qb.ix_jobs_terminal_finished")
    op.execute("ALTER TABLE qb.account DROP COLUMN IF EXISTS session_version")
    op.execute("DROP TABLE IF EXISTS qb.mcq_assertion_facets")
    op.execute("DROP TABLE IF EXISTS qb.document_learn_state")
