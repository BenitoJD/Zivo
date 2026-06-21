"""QB app + infra schema (qb.* DDL).

Revision ID: 002_qb_schema
Revises: 001_intel_foundation
Create Date: 2026-06-22

Source of truth: backend/schema/qb_app.sql, backend/schema/qb_infra.sql
"""

from typing import Sequence, Union

from alembic import op

from app.migration_sql import execute_sql_file

revision: str = "002_qb_schema"
down_revision: Union[str, None] = "001_intel_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    execute_sql_file("qb_app.sql")
    execute_sql_file("qb_infra.sql")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS qb CASCADE")
