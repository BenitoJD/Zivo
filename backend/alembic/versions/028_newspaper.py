"""Newspaper practice — editions, channel settings, aliases.

Revision ID: 028_newspaper
Revises: 027_system_design
Create Date: 2026-07-24

Shared newspaper editions cooked once for practice (MCQs + tutor). Learners
never see the PDF. Channel pointer is mutable admin config.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "028_newspaper"
down_revision: Union[str, None] = "027_system_design"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.newspaper_settings (
          id              INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
          channel_ref     TEXT NOT NULL DEFAULT '',
          channel_label   TEXT NOT NULL DEFAULT '',
          sync_cursor     BIGINT,
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        INSERT INTO qb.newspaper_settings (id, channel_ref, channel_label)
        VALUES (1, '', '')
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.newspaper_paper_alias (
          alias_key       TEXT PRIMARY KEY,
          paper_slug      TEXT NOT NULL,
          paper_title     TEXT NOT NULL,
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.newspaper_edition (
          id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          paper_slug        TEXT NOT NULL,
          paper_title       TEXT NOT NULL,
          edition_date      DATE NOT NULL,
          document_id       UUID REFERENCES qb.documents (id) ON DELETE SET NULL,
          telegram_msg_id   BIGINT,
          location_raw      TEXT NOT NULL DEFAULT '',
          status            TEXT NOT NULL DEFAULT 'pending',
          created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT newspaper_edition_status_check
            CHECK (status IN ('pending', 'indexing', 'ready', 'failed', 'purged')),
          CONSTRAINT newspaper_edition_paper_day_unique
            UNIQUE (paper_slug, edition_date)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_newspaper_edition_ready_date
        ON qb.newspaper_edition (edition_date DESC)
        WHERE status = 'ready'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_newspaper_edition_paper
        ON qb.newspaper_edition (paper_slug, edition_date DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qb.newspaper_edition")
    op.execute("DROP TABLE IF EXISTS qb.newspaper_paper_alias")
    op.execute("DROP TABLE IF EXISTS qb.newspaper_settings")
