"""Newspaper brand allowlist — pick which papers to ingest/show.

Revision ID: 029_newspaper_brands
Revises: 028_newspaper
Create Date: 2026-07-24
"""

from typing import Sequence, Union

from alembic import op

revision: str = "029_newspaper_brands"
down_revision: Union[str, None] = "028_newspaper"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE qb.newspaper_settings
          ADD COLUMN IF NOT EXISTS allowlist_only BOOLEAN NOT NULL DEFAULT false
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.newspaper_brand (
          paper_slug     TEXT PRIMARY KEY,
          paper_title    TEXT NOT NULL,
          enabled        BOOLEAN NOT NULL DEFAULT false,
          first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_newspaper_brand_enabled
        ON qb.newspaper_brand (enabled)
        WHERE enabled = true
        """
    )
    # Seed brands from editions already seen so admin has something to toggle.
    op.execute(
        """
        INSERT INTO qb.newspaper_brand (paper_slug, paper_title, enabled, first_seen_at, updated_at)
        SELECT paper_slug, MAX(paper_title), false, MIN(created_at), now()
        FROM qb.newspaper_edition
        GROUP BY paper_slug
        ON CONFLICT (paper_slug) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qb.newspaper_brand")
    op.execute(
        """
        ALTER TABLE qb.newspaper_settings
          DROP COLUMN IF EXISTS allowlist_only
        """
    )
