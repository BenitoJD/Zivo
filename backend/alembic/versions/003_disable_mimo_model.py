"""Disable MiMo v2.5 chat model; promote GLM 4.7 default when needed.

Revision ID: 003_disable_mimo_model
Revises: 002_qb_schema
Create Date: 2026-06-24
"""

from typing import Sequence, Union

from alembic import op

revision: str = "003_disable_mimo_model"
down_revision: Union[str, None] = "002_qb_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE qb.llm_models AS m
        SET is_enabled = false,
            is_default = false
        FROM qb.llm_providers AS p
        WHERE m.provider_id = p.id
          AND p.slug = 'openai'
          AND m.slug = 'mimo-v2.5'
        """
    )
    op.execute(
        """
        UPDATE qb.llm_models AS m
        SET is_default = true
        FROM qb.llm_providers AS p
        WHERE m.provider_id = p.id
          AND p.slug = 'zai'
          AND m.slug = 'glm-4.7'
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
        UPDATE qb.llm_models AS m
        SET is_enabled = true
        FROM qb.llm_providers AS p
        WHERE m.provider_id = p.id
          AND p.slug = 'openai'
          AND m.slug = 'mimo-v2.5'
        """
    )
