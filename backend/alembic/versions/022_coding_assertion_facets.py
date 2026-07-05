"""Coding-question facets + vocab seeds for the LeetCode-style practice bank.

Revision ID: 022_coding_assertion_facets
Revises: 021_document_resume
Create Date: 2026-07-04

Two things land here:
1. ``qb.coding_assertion_facets`` — hot fields promoted out of the coding
   assertion payload (title, difficulty, language_id, sample/hidden test
   counts) so the problem-list query doesn't touch JSONB. Mirrors
   ``qb.mcq_assertion_facets`` (migration 006).
2. Vocabulary concept rows the runtime resolves via ``intel.concept.uri``:
   ``/vocab/assertion/question.coding`` (assertion type) and
   ``/vocab/metric/coding.passed`` (measurement metric for a coding submit).
   Kept here — not in ``seed_question_vocab.py`` — so a fresh prod DB gets
   them via ``alembic upgrade head`` without a separate step.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "022_coding_assertion_facets"
down_revision: Union[str, None] = "021_document_resume"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- facets table --------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.coding_assertion_facets (
          assertion_id UUID PRIMARY KEY,
          artifact_id UUID NOT NULL,
          page_number INTEGER NOT NULL DEFAULT 0,
          sequence INTEGER NOT NULL DEFAULT 0,
          title TEXT,
          difficulty TEXT,
          language_id INTEGER,
          sample_test_count INTEGER NOT NULL DEFAULT 0,
          hidden_test_count INTEGER NOT NULL DEFAULT 0,
          CONSTRAINT coding_assertion_facets_assertion_fk
            FOREIGN KEY (assertion_id) REFERENCES intel.assertion(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_coding_facets_artifact_page
        ON qb.coding_assertion_facets (artifact_id, page_number)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_coding_facets_difficulty
        ON qb.coding_assertion_facets (difficulty)
        """
    )

    # --- vocabulary ----------------------------------------------------------
    op.execute(
        """
        INSERT INTO intel.concept (uri, family, label)
        VALUES
          ('/vocab/assertion/question.coding', 'assertion_type', 'Coding question'),
          ('/vocab/metric/coding.passed', 'metric_type', 'Coding tests passed')
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS qb.ix_coding_facets_difficulty")
    op.execute("DROP INDEX IF EXISTS qb.ix_coding_facets_artifact_page")
    op.execute("DROP TABLE IF EXISTS qb.coding_assertion_facets")
    op.execute(
        """
        DELETE FROM intel.concept
        WHERE uri IN (
          '/vocab/assertion/question.coding',
          '/vocab/metric/coding.passed'
        )
        """
    )
