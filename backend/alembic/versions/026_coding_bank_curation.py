"""Coding problem bank — curation columns for LeetCode-style browse/publish.

Revision ID: 026_coding_bank_curation
Revises: 025_brainstorm_ideas
Create Date: 2026-07-23

Additive on ``qb.coding_assertion_facets``:
- ``published`` — hide drafts from the public/practice list
- ``origin`` — ``generated`` (from uploaded material) vs ``curated`` (human-authored)
- ``tags`` — TEXT[] for browse filters (arrays/hashing/etc.)
- ``artifact_id`` nullable — curated bank problems are not tied to a document

Also seeds the ``coding-bank`` intel.source slug so curated assertions have a
stable provenance without inventing a fake document.
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text as _sa_text

revision: str = "026_coding_bank_curation"
down_revision: Union[str, None] = "025_brainstorm_ideas"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE qb.coding_assertion_facets
          ALTER COLUMN artifact_id DROP NOT NULL
        """
    )
    op.execute(
        """
        ALTER TABLE qb.coding_assertion_facets
          ADD COLUMN IF NOT EXISTS published BOOLEAN NOT NULL DEFAULT true,
          ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'generated',
          ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}'
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'coding_facets_origin_check'
          ) THEN
            ALTER TABLE qb.coding_assertion_facets
              ADD CONSTRAINT coding_facets_origin_check
              CHECK (origin IN ('generated', 'curated'));
          END IF;
        END $$
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_coding_facets_published
        ON qb.coding_assertion_facets (published)
        WHERE published = true
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_coding_facets_tags
        ON qb.coding_assertion_facets USING GIN (tags)
        """
    )

    bind = op.get_bind()
    domain_id = bind.execute(
        _sa_text("SELECT id FROM intel.concept WHERE uri = '/vocab/domain/user_learning'")
    ).scalar()
    def _seed_coding_bank() -> None:
        bind.execute(
            _sa_text(
                "INSERT INTO intel.source (slug, name, domain_concept_id) "
                "VALUES ('coding-bank', 'Coding problem bank', :domain) "
                "ON CONFLICT (slug) DO NOTHING"
            ),
            {"domain": domain_id},
        )

    {True: _seed_coding_bank, False: lambda: None}[domain_id is not None]()


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(_sa_text("DELETE FROM intel.source WHERE slug = 'coding-bank'"))
    op.execute("DROP INDEX IF EXISTS qb.ix_coding_facets_tags")
    op.execute("DROP INDEX IF EXISTS qb.ix_coding_facets_published")
    op.execute(
        "ALTER TABLE qb.coding_assertion_facets "
        "DROP CONSTRAINT IF EXISTS coding_facets_origin_check"
    )
    op.execute(
        """
        ALTER TABLE qb.coding_assertion_facets
          DROP COLUMN IF EXISTS tags,
          DROP COLUMN IF EXISTS origin,
          DROP COLUMN IF EXISTS published
        """
    )
    # Curated rows have no artifact — drop them before restoring NOT NULL.
    op.execute("DELETE FROM qb.coding_assertion_facets WHERE artifact_id IS NULL")
    op.execute(
        """
        ALTER TABLE qb.coding_assertion_facets
          ALTER COLUMN artifact_id SET NOT NULL
        """
    )
