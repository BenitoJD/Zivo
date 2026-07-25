"""Cluster-wide rate-limit hit counters.

Revision ID: 033_rate_limit_hits
Revises: 032_seo_learn_content
Create Date: 2026-07-25

Per-process in-memory rate limiting was blind across pods: with N API replicas
the effective limit was ``rate_limit_per_minute * N``. This table backs a
cluster-safe counter using a fixed-minute bucket keyed by client IP, written
via ``INSERT ... ON CONFLICT DO UPDATE ... RETURNING`` (same atomic pattern as
``demo_usage``). Old buckets age out via a periodic sweep in the limiter.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "033_rate_limit_hits"
down_revision: Union[str, None] = "032_seo_learn_content"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.rate_limit_hit (
          bucket      INTEGER NOT NULL,
          client_key  VARCHAR(80) NOT NULL,
          hit_count   INTEGER NOT NULL DEFAULT 0,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT rate_limit_hit_pk PRIMARY KEY (bucket, client_key)
        )
        """
    )
    # Sweep support: cheap range delete of aged buckets.
    op.execute(
        "CREATE INDEX IF NOT EXISTS rate_limit_hit_bucket_idx ON qb.rate_limit_hit (bucket)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qb.rate_limit_hit")
