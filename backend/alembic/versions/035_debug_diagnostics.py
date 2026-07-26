"""Debug diagnostics — cook jobs, assertion facets, vocabulary.

Revision ID: 035_debug_diagnostics
Revises: 034_document_learner_state
Create Date: 2026-07-26

- ``qb.debug_cook_job`` — intake → cook → review lifecycle
- ``qb.debug_assertion_facets`` — hot fields for browse/review (mirrors coding facets)
- Vocab: question.debug, metric/debug.understood, activity/cook_debug
- ``debug-bank`` intel.source for curated bank provenance
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text as _sa_text

revision: str = "035_debug_diagnostics"
down_revision: Union[str, None] = "034_document_learner_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.debug_cook_job (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          owner_user_id   UUID REFERENCES qb.account (id) ON DELETE SET NULL,
          source_type     TEXT NOT NULL DEFAULT 'paste',
          source_ref      TEXT,
          material        TEXT NOT NULL DEFAULT '',
          brief           TEXT NOT NULL DEFAULT '',
          scenario_count  INTEGER NOT NULL DEFAULT 3,
          status          TEXT NOT NULL DEFAULT 'queued',
          origin          TEXT NOT NULL DEFAULT 'generated',
          review_status   TEXT NOT NULL DEFAULT 'draft',
          published       BOOLEAN NOT NULL DEFAULT false,
          error           TEXT,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          completed_at    TIMESTAMPTZ,
          CONSTRAINT debug_cook_job_status_check
            CHECK (status IN ('queued', 'cooking', 'done', 'failed')),
          CONSTRAINT debug_cook_job_review_status_check
            CHECK (review_status IN ('draft', 'pending_review', 'approved', 'rejected')),
          CONSTRAINT debug_cook_job_origin_check
            CHECK (origin IN ('generated', 'curated', 'contributed')),
          CONSTRAINT debug_cook_job_source_type_check
            CHECK (source_type IN ('paste', 'upload', 'document_page', 'admin_author'))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_debug_cook_job_owner
        ON qb.debug_cook_job (owner_user_id, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_debug_cook_job_review
        ON qb.debug_cook_job (review_status)
        WHERE review_status = 'pending_review'
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.debug_assertion_facets (
          assertion_id    UUID PRIMARY KEY,
          cook_job_id     UUID REFERENCES qb.debug_cook_job (id) ON DELETE SET NULL,
          artifact_id     UUID,
          page_number     INTEGER NOT NULL DEFAULT 0,
          sequence        INTEGER NOT NULL DEFAULT 0,
          title           TEXT,
          scenario_type   TEXT NOT NULL DEFAULT 'code_reading',
          difficulty      TEXT,
          step_count      INTEGER NOT NULL DEFAULT 0,
          published       BOOLEAN NOT NULL DEFAULT false,
          origin          TEXT NOT NULL DEFAULT 'generated',
          review_status   TEXT NOT NULL DEFAULT 'draft',
          owner_user_id   UUID REFERENCES qb.account (id) ON DELETE SET NULL,
          tags            TEXT[] NOT NULL DEFAULT '{}',
          CONSTRAINT debug_assertion_facets_assertion_fk
            FOREIGN KEY (assertion_id) REFERENCES intel.assertion(id) ON DELETE CASCADE,
          CONSTRAINT debug_facets_origin_check
            CHECK (origin IN ('generated', 'curated', 'contributed')),
          CONSTRAINT debug_facets_review_status_check
            CHECK (review_status IN ('draft', 'pending_review', 'approved', 'rejected'))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_debug_facets_published
        ON qb.debug_assertion_facets (published)
        WHERE published = true
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_debug_facets_review
        ON qb.debug_assertion_facets (review_status)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_debug_facets_cook_job
        ON qb.debug_assertion_facets (cook_job_id)
        WHERE cook_job_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_debug_facets_tags
        ON qb.debug_assertion_facets USING GIN (tags)
        """
    )

    op.execute(
        """
        INSERT INTO intel.concept (uri, family, label)
        VALUES
          ('/vocab/assertion/question.debug', 'assertion_type', 'Debug diagnostic question'),
          ('/vocab/metric/debug.understood', 'metric_type', 'Debug scenario understood'),
          ('/vocab/activity/cook_debug', 'activity_type', 'Cook debug scenarios')
        ON CONFLICT DO NOTHING
        """
    )

    bind = op.get_bind()
    domain_id = bind.execute(
        _sa_text("SELECT id FROM intel.concept WHERE uri = '/vocab/domain/user_learning'")
    ).scalar()
    if domain_id is not None:
        bind.execute(
            _sa_text(
                "INSERT INTO intel.source (slug, name, domain_concept_id) "
                "VALUES ('debug-bank', 'Debug diagnostics bank', :domain) "
                "ON CONFLICT (slug) DO NOTHING"
            ),
            {"domain": domain_id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(_sa_text("DELETE FROM intel.source WHERE slug = 'debug-bank'"))
    op.execute("DROP INDEX IF EXISTS qb.ix_debug_facets_tags")
    op.execute("DROP INDEX IF EXISTS qb.ix_debug_facets_cook_job")
    op.execute("DROP INDEX IF EXISTS qb.ix_debug_facets_review")
    op.execute("DROP INDEX IF EXISTS qb.ix_debug_facets_published")
    op.execute("DROP TABLE IF EXISTS qb.debug_assertion_facets")
    op.execute("DROP INDEX IF EXISTS qb.ix_debug_cook_job_review")
    op.execute("DROP INDEX IF EXISTS qb.ix_debug_cook_job_owner")
    op.execute("DROP TABLE IF EXISTS qb.debug_cook_job")
    op.execute(
        """
        DELETE FROM intel.concept
        WHERE uri IN (
          '/vocab/assertion/question.debug',
          '/vocab/metric/debug.understood',
          '/vocab/activity/cook_debug'
        )
        """
    )
