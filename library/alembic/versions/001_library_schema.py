"""Create exclusive library schema marker. Shared qb/intel tables stay on product Alembic.

Revision ID: 001_library_schema
Revises:
"""

from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "001_library_schema"
down_revision: Union[str, None] = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS library")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS library")
