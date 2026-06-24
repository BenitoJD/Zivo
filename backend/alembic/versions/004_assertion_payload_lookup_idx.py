"""Add expression indexes for intel.assertion + qb.jobs payload lookups and a
created_at index on qb.llm_response_cache for TTL purge.

The hot read paths filter intel.assertion on `payload->>'artifact_id'` and
`(payload->>'page_number')::int`, and order by `(payload->>'sequence')::int`
(count_assertions_on_page, _count_available, next_assertion_id, etc.). The
existing GIN index on payload does not serve `->>` equality / cast / ORDER BY,
so every learn-queue poll was a full scan of the doc's assertions. Partial
(`status='active'`) expression btree indexes turn these into index lookups.

The qb.jobs index serves the generate-questions guard queries that filter on
`payload->>'document_id'` + `payload->>'page_number'`.

The qb.llm_response_cache created_at index backs the TTL purge job that evicts
stale cached chat responses (the table otherwise grows unbounded).

Revision ID: 004_assertion_payload_lookup_idx
Revises: 003_disable_mimo_model
Create Date: 2026-06-24
"""

from typing import Sequence, Union

from alembic import op

revision: str = "004_assertion_payload_lookup_idx"
down_revision: Union[str, None] = "003_disable_mimo_model"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # intel.assertion — per-doc, per-page question lookups (the learn-queue hot path).
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS assertion_artifact_page_idx
            ON intel.assertion ((payload->>'artifact_id'), ((payload->>'page_number')::int))
            WHERE status = 'active'
        """
    )
    # intel.assertion — per-doc concept listing + purge (page_number not needed here).
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS assertion_artifact_concept_idx
            ON intel.assertion ((payload->>'artifact_id'))
            WHERE status = 'active'
        """
    )
    # qb.jobs — generate.questions guard queries (active-job / cancel / dedup checks).
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS jobs_generate_lookup_idx
            ON qb.jobs ((payload->>'document_id'), (payload->>'page_number'))
            WHERE name = 'generate.questions'
        """
    )
    # qb.llm_response_cache — backs the TTL purge (created_at filter).
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS llm_response_cache_created_idx
            ON qb.llm_response_cache (created_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS qb.llm_response_cache_created_idx")
    op.execute("DROP INDEX IF EXISTS qb.jobs_generate_lookup_idx")
    op.execute("DROP INDEX IF EXISTS intel.assertion_artifact_concept_idx")
    op.execute("DROP INDEX IF EXISTS intel.assertion_artifact_page_idx")
