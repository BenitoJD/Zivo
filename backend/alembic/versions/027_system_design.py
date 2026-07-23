"""System Design mastery — concepts, problem bank, sessions.

Revision ID: 027_system_design
Revises: 026_coding_bank_curation
Create Date: 2026-07-23

Separate product surface (not Interview). Skill path concepts, curated cases,
and per-learner sessions for the design → grade → teach-gap → next loop.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "027_system_design"
down_revision: Union[str, None] = "026_coding_bank_curation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.sd_concept (
          key            TEXT PRIMARY KEY,
          title          TEXT NOT NULL,
          blurb          TEXT NOT NULL DEFAULT '',
          sort_order     INTEGER NOT NULL DEFAULT 0,
          prerequisites  TEXT[] NOT NULL DEFAULT '{}',
          created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.sd_problem (
          id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          slug              TEXT NOT NULL UNIQUE,
          title             TEXT NOT NULL,
          prompt            TEXT NOT NULL,
          constraints       TEXT NOT NULL DEFAULT '',
          difficulty        TEXT NOT NULL DEFAULT 'medium',
          concept_keys      TEXT[] NOT NULL DEFAULT '{}',
          rubric_hints      JSONB NOT NULL DEFAULT '{}'::jsonb,
          reference_design  TEXT NOT NULL DEFAULT '',
          published         BOOLEAN NOT NULL DEFAULT true,
          sort_order        INTEGER NOT NULL DEFAULT 0,
          created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT sd_problem_difficulty_check
            CHECK (difficulty IN ('easy', 'medium', 'hard'))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_sd_problem_published
        ON qb.sd_problem (published, sort_order)
        WHERE published = true
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_sd_problem_concepts
        ON qb.sd_problem USING GIN (concept_keys)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.sd_session (
          id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          problem_id            UUID NOT NULL REFERENCES qb.sd_problem (id) ON DELETE CASCADE,
          account_id            UUID REFERENCES qb.account (id) ON DELETE SET NULL,
          guest_id              TEXT,
          status                TEXT NOT NULL DEFAULT 'active',
          design                JSONB NOT NULL DEFAULT '{}'::jsonb,
          scores                JSONB NOT NULL DEFAULT '{}'::jsonb,
          feedback              JSONB NOT NULL DEFAULT '{}'::jsonb,
          weak_concepts         TEXT[] NOT NULL DEFAULT '{}',
          lesson                JSONB NOT NULL DEFAULT '{}'::jsonb,
          recommended_next_id   UUID REFERENCES qb.sd_problem (id) ON DELETE SET NULL,
          created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT sd_session_status_check
            CHECK (status IN ('active', 'done')),
          CONSTRAINT sd_session_subject_check
            CHECK (account_id IS NOT NULL OR guest_id IS NOT NULL)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_sd_session_account_active
        ON qb.sd_session (account_id, status, updated_at DESC)
        WHERE account_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_sd_session_guest_active
        ON qb.sd_session (guest_id, status, updated_at DESC)
        WHERE guest_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qb.sd_session")
    op.execute("DROP TABLE IF EXISTS qb.sd_problem")
    op.execute("DROP TABLE IF EXISTS qb.sd_concept")
