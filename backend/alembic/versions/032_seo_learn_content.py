"""SEO /learn content engine — posts, authors, topic queue, settings.

Revision ID: 032_seo_learn_content
Revises: 031_hindu_newspaper_v1
Create Date: 2026-07-24

Public blog posts cooked from newspaper/upload/SD bank/topic queue.
cook_enabled defaults false until first prod smoke.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "032_seo_learn_content"
down_revision: Union[str, None] = "031_hindu_newspaper_v1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.seo_settings (
          id                INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
          cook_enabled      BOOLEAN NOT NULL DEFAULT false,
          soft_max_per_day  INTEGER NOT NULL DEFAULT 20,
          updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        INSERT INTO qb.seo_settings (id, cook_enabled, soft_max_per_day)
        VALUES (1, false, 20)
        ON CONFLICT (id) DO NOTHING
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.seo_author (
          id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          name        TEXT NOT NULL UNIQUE,
          active      BOOLEAN NOT NULL DEFAULT true,
          sort_order  INTEGER NOT NULL DEFAULT 0,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.seo_post (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          slug                TEXT NOT NULL,
          title               TEXT NOT NULL,
          lede                TEXT NOT NULL DEFAULT '',
          body_md             TEXT NOT NULL DEFAULT '',
          format              TEXT NOT NULL DEFAULT 'explainer',
          stream              TEXT NOT NULL DEFAULT 'general',
          author_name         TEXT NOT NULL DEFAULT '',
          status              TEXT NOT NULL DEFAULT 'draft',
          topic_fingerprint   TEXT NOT NULL,
          embedding           vector(384),
          source_kind         TEXT NOT NULL,
          source_ref          JSONB NOT NULL DEFAULT '{}'::jsonb,
          faq_jsonld          JSONB NOT NULL DEFAULT '[]'::jsonb,
          cta_kind            TEXT NOT NULL DEFAULT 'practice',
          artifact_id         UUID REFERENCES qb.documents (id) ON DELETE SET NULL,
          published_at        TIMESTAMPTZ,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT seo_post_format_check
            CHECK (format IN ('explainer', 'faq', 'list')),
          CONSTRAINT seo_post_stream_check
            CHECK (stream IN ('general', 'system_design')),
          CONSTRAINT seo_post_status_check
            CHECK (status IN ('draft', 'published', 'unpublished')),
          CONSTRAINT seo_post_source_kind_check
            CHECK (source_kind IN ('newspaper', 'upload', 'sd_bank', 'topic_queue'))
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_seo_post_slug
          ON qb.seo_post (slug)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_seo_post_topic_fingerprint_published
          ON qb.seo_post (topic_fingerprint)
          WHERE status = 'published'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_seo_post_published_stream
          ON qb.seo_post (stream, published_at DESC)
          WHERE status = 'published'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_seo_post_published_at
          ON qb.seo_post (published_at DESC)
          WHERE status = 'published'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_seo_post_embedding_hnsw
          ON qb.seo_post USING hnsw (embedding vector_cosine_ops)
          WHERE embedding IS NOT NULL AND status = 'published'
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.seo_post_assertion (
          post_id       UUID NOT NULL REFERENCES qb.seo_post (id) ON DELETE CASCADE,
          assertion_id  UUID NOT NULL,
          position      INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (post_id, assertion_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_seo_post_assertion_post
          ON qb.seo_post_assertion (post_id, position)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.seo_topic_queue (
          id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          topic_key     TEXT NOT NULL UNIQUE,
          stream        TEXT NOT NULL DEFAULT 'system_design',
          title_hint    TEXT NOT NULL,
          angle_prompt  TEXT NOT NULL DEFAULT '',
          priority      INTEGER NOT NULL DEFAULT 100,
          last_used_at  TIMESTAMPTZ,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT seo_topic_queue_stream_check
            CHECK (stream IN ('general', 'system_design'))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_seo_topic_queue_pick
          ON qb.seo_topic_queue (stream, priority ASC, last_used_at ASC NULLS FIRST)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.seo_cook_attempt (
          source_kind   TEXT NOT NULL,
          source_key    TEXT NOT NULL,
          outcome       TEXT NOT NULL,
          reason        TEXT NOT NULL DEFAULT '',
          post_id       UUID REFERENCES qb.seo_post (id) ON DELETE SET NULL,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (source_kind, source_key),
          CONSTRAINT seo_cook_attempt_outcome_check
            CHECK (outcome IN ('published', 'skipped'))
        )
        """
    )

    # Bylines — Benito JD + Indian name roster
    op.execute(
        """
        INSERT INTO qb.seo_author (name, active, sort_order) VALUES
          ('Benito JD', true, 10),
          ('Ananya Sharma', true, 20),
          ('Arjun Mehta', true, 30),
          ('Priya Nair', true, 40),
          ('Rohan Iyer', true, 50),
          ('Kavya Reddy', true, 60),
          ('Vikram Singh', true, 70),
          ('Meera Krishnan', true, 80),
          ('Aditya Patel', true, 90),
          ('Sneha Gupta', true, 100)
        ON CONFLICT (name) DO NOTHING
        """
    )

    # Evergreen SD/tech seeds for daily floor
    op.execute(
        """
        INSERT INTO qb.seo_topic_queue (topic_key, stream, title_hint, angle_prompt, priority) VALUES
          ('caching-basics', 'system_design', 'How caching actually works',
           'Explain caching layers, hit rate, and when cache hurts. One clear idea.', 10),
          ('cap-theorem', 'system_design', 'CAP theorem without the fog',
           'Explain consistency, availability, partition tolerance with one concrete tradeoff.', 20),
          ('rate-limits', 'system_design', 'Rate limiting that protects systems',
           'Token bucket vs sliding window; where to put limits; what users feel.', 30),
          ('message-queues', 'system_design', 'Why systems use message queues',
           'Decoupling, retries, poison messages. Teach the idea, not a vendor.', 40),
          ('load-balancing', 'system_design', 'Load balancing choices that matter',
           'L4 vs L7, sticky sessions, health checks. Short and concrete.', 50),
          ('database-indexing', 'system_design', 'Database indexes that earn their keep',
           'B-tree basics, selectivity, write cost. One mistake learners make.', 60),
          ('idempotency', 'system_design', 'Idempotent APIs under retries',
           'Why retries duplicate work; keys and exactly-once illusions.', 70),
          ('cdn-edge', 'system_design', 'CDNs and the edge',
           'What to cache at the edge, cache keys, and invalidation pain.', 80),
          ('sharding', 'system_design', 'When to shard a database',
           'Hot keys, rebalancing, and why premature sharding hurts.', 90),
          ('observability', 'system_design', 'Logs, metrics, and traces',
           'What each answers; one example of finding a production bug.', 100)
        ON CONFLICT (topic_key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qb.seo_cook_attempt")
    op.execute("DROP TABLE IF EXISTS qb.seo_post_assertion")
    op.execute("DROP TABLE IF EXISTS qb.seo_post")
    op.execute("DROP TABLE IF EXISTS qb.seo_topic_queue")
    op.execute("DROP TABLE IF EXISTS qb.seo_author")
    op.execute("DROP TABLE IF EXISTS qb.seo_settings")
