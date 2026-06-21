"""Intel foundation schema (intel.* DDL).

Revision ID: 001_intel_foundation
Revises:
Create Date: 2026-06-22

Source of truth: backend/schema/intel_foundation.sql
"""

from typing import Sequence, Union

from alembic import op

from app.migration_sql import execute_sql_file

revision: str = "001_intel_foundation"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    execute_sql_file("intel_foundation.sql")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS intel CASCADE")
