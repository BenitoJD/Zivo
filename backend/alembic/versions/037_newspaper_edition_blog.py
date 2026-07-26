"""Newspaper edition public blog — link editions to seo_post.

Revision ID: 037_newspaper_edition_blog
Revises: 036_disable_stepfun
Create Date: 2026-07-26

One digest post per ready edition at /learn/newspaper/{slug}/{date}.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "037_newspaper_edition_blog"
down_revision: Union[str, None] = "036_disable_stepfun"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE qb.newspaper_edition
          ADD COLUMN IF NOT EXISTS blog_post_id UUID
            REFERENCES qb.seo_post (id) ON DELETE SET NULL,
          ADD COLUMN IF NOT EXISTS blog_status TEXT NOT NULL DEFAULT 'none',
          ADD COLUMN IF NOT EXISTS blog_published_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        ALTER TABLE qb.newspaper_edition
          DROP CONSTRAINT IF EXISTS newspaper_edition_blog_status_check
        """
    )
    op.execute(
        """
        ALTER TABLE qb.newspaper_edition
          ADD CONSTRAINT newspaper_edition_blog_status_check
            CHECK (blog_status IN ('none', 'pending', 'published', 'skipped', 'failed'))
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_newspaper_edition_blog_published
          ON qb.newspaper_edition (paper_slug, edition_date DESC)
          WHERE blog_status = 'published'
        """
    )
    op.execute(
        """
        ALTER TABLE qb.seo_post
          DROP CONSTRAINT IF EXISTS seo_post_source_kind_check
        """
    )
    op.execute(
        """
        ALTER TABLE qb.seo_post
          ADD CONSTRAINT seo_post_source_kind_check
            CHECK (source_kind IN (
              'newspaper', 'upload', 'sd_bank', 'topic_queue', 'newspaper_edition'
            ))
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE qb.seo_post
          DROP CONSTRAINT IF EXISTS seo_post_source_kind_check
        """
    )
    op.execute(
        """
        ALTER TABLE qb.seo_post
          ADD CONSTRAINT seo_post_source_kind_check
            CHECK (source_kind IN ('newspaper', 'upload', 'sd_bank', 'topic_queue'))
        """
    )
    op.execute("DROP INDEX IF EXISTS ix_newspaper_edition_blog_published")
    op.execute(
        """
        ALTER TABLE qb.newspaper_edition
          DROP CONSTRAINT IF EXISTS newspaper_edition_blog_status_check
        """
    )
    op.execute(
        """
        ALTER TABLE qb.newspaper_edition
          DROP COLUMN IF EXISTS blog_published_at,
          DROP COLUMN IF EXISTS blog_status,
          DROP COLUMN IF EXISTS blog_post_id
        """
    )
