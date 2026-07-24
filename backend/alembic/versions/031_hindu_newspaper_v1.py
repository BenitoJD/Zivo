"""Hindu newspaper v1 — allowlist The Hindu; aliases learned via LLM.

Revision ID: 031_hindu_newspaper_v1
Revises: 030_google_oauth
Create Date: 2026-07-24

v1 cooks only The Hindu. Filename/caption → paper mapping is NOT hardcoded;
ingest uses LLM classification and writes ``qb.newspaper_paper_alias`` as it learns.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "031_hindu_newspaper_v1"
down_revision: Union[str, None] = "030_google_oauth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE qb.newspaper_settings
        SET allowlist_only = true, updated_at = now()
        WHERE id = 1
        """
    )
    op.execute(
        """
        INSERT INTO qb.newspaper_brand (paper_slug, paper_title, enabled, first_seen_at, updated_at)
        VALUES ('the-hindu', 'The Hindu', true, now(), now())
        ON CONFLICT (paper_slug) DO UPDATE SET
          paper_title = EXCLUDED.paper_title,
          enabled = true,
          updated_at = now()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE qb.newspaper_brand
        SET enabled = false, updated_at = now()
        WHERE paper_slug = 'the-hindu'
        """
    )
    op.execute(
        """
        UPDATE qb.newspaper_settings
        SET allowlist_only = false, updated_at = now()
        WHERE id = 1
        """
    )
