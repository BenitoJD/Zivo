"""Create auth schema: account + rate_limit_hit.

Revision ID: 001_auth_schema
Revises:
"""

from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "001_auth_schema"
down_revision: Union[str, None] = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS auth")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS auth.account (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          username        VARCHAR(32) NOT NULL UNIQUE,
          password_hash   VARCHAR(255),
          email           VARCHAR(320),
          google_sub      VARCHAR(64),
          is_admin        BOOLEAN NOT NULL DEFAULT false,
          session_version INTEGER NOT NULL DEFAULT 0,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS account_username_idx ON auth.account (username)"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS account_email_uidx
          ON auth.account (email) WHERE email IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS account_google_sub_uidx
          ON auth.account (google_sub) WHERE google_sub IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS auth.rate_limit_hit (
          bucket      INTEGER NOT NULL,
          client_key  TEXT NOT NULL,
          hit_count   INTEGER NOT NULL DEFAULT 1,
          PRIMARY KEY (bucket, client_key)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS auth.rate_limit_hit")
    op.execute("DROP TABLE IF EXISTS auth.account")
    op.execute("DROP SCHEMA IF EXISTS auth")
