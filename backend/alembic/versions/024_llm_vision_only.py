"""Vision-only LLM models — keep a metered vision model out of the text pool.

Revision ID: 024_llm_vision_only
Revises: 023_document_mains
Create Date: 2026-07-22

A model with `vision_only` is selectable only for require_vision calls (OCR /
image), never for ordinary text generation or failover — so e.g. MiMo on the
metered plan is billed for vision alone. Additive + defaulted, so old code
(which never SELECTs this column) is unaffected; new code reads it. The deploy
runs this migration BEFORE rolling the app, so the column exists when new pods boot.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "024_llm_vision_only"
down_revision: Union[str, None] = "023_document_mains"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE qb.llm_models ADD COLUMN IF NOT EXISTS vision_only BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE qb.llm_models DROP COLUMN IF EXISTS vision_only")
