"""Per-provider concurrency limit.

Providers cap in-flight requests at very different levels — Step Fun rejects the
9th concurrent call outright ("concurrency reached, current: 9, limit: 8") while
others are far more permissive. The single process-wide LLM_MAX_CONCURRENT env
can't express that, so the ceiling belongs next to the provider it describes.

Additive + nullable: NULL means "no provider-specific limit" (the process-wide
cap still applies), so a pod that predates this migration behaves exactly as
before and a pod that postdates it reads NULL as unlimited.

Revision ID: 044_provider_max_concurrency
Revises: 043_offline_packs
"""

from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "044_provider_max_concurrency"
down_revision: Union[str, None] = "043_offline_packs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE qb.llm_providers
        ADD COLUMN IF NOT EXISTS max_concurrency INTEGER
        """
    )
    # Seed the one provider with a known hard ceiling. Left NULL elsewhere so no
    # provider silently gains a limit it doesn't have.
    op.execute(
        """
        UPDATE qb.llm_providers
        SET max_concurrency = 8
        WHERE slug = 'stepfun' AND max_concurrency IS NULL
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE qb.llm_providers DROP COLUMN IF EXISTS max_concurrency")
