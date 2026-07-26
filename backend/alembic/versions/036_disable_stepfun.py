"""Disable Step Fun provider and models; prefer DeepSeek default.

Revision ID: 036_disable_stepfun
Revises: 035_debug_diagnostics
Create Date: 2026-07-26
"""

from typing import Sequence, Union

from alembic import op

revision: str = "036_disable_stepfun"
down_revision: Union[str, None] = "035_debug_diagnostics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE qb.llm_providers
        SET is_enabled = false
        WHERE slug = 'stepfun'
        """
    )
    op.execute(
        """
        UPDATE qb.llm_models AS m
        SET is_enabled = false,
            is_default = false
        FROM qb.llm_providers AS p
        WHERE m.provider_id = p.id
          AND p.slug = 'stepfun'
        """
    )
    op.execute(
        """
        UPDATE qb.llm_models AS m
        SET is_default = true
        FROM qb.llm_providers AS p
        WHERE m.provider_id = p.id
          AND p.slug = 'deepseek'
          AND m.slug = 'deepseek-v4-flash'
          AND m.kind = 'chat'
          AND m.is_enabled = true
          AND NOT EXISTS (
            SELECT 1
            FROM qb.llm_models
            WHERE kind = 'chat'
              AND is_default = true
              AND is_enabled = true
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE qb.llm_providers
        SET is_enabled = true
        WHERE slug = 'stepfun'
        """
    )
    op.execute(
        """
        UPDATE qb.llm_models AS m
        SET is_enabled = true
        FROM qb.llm_providers AS p
        WHERE m.provider_id = p.id
          AND p.slug = 'stepfun'
          AND m.slug IN ('step-3.5-flash', 'step-3.7-flash')
        """
    )
