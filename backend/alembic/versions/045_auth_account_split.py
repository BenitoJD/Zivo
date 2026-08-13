"""Split identity into auth.account; qb.account becomes a product stub.

Copies credentials off qb.account into auth.account (create-if-missing so this
revision is safe whether or not the auth service Alembic has already run), then
drops secret columns from qb.account. Product FKs keep pointing at qb.account.

Revision ID: 045_auth_account_split
Revises: 044_provider_max_concurrency
"""

from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "045_auth_account_split"
down_revision: Union[str, None] = "044_provider_max_concurrency"
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
    op.execute(
        """
        INSERT INTO auth.account (
          id, username, password_hash, email, google_sub,
          is_admin, session_version, created_at
        )
        SELECT
          id, username, password_hash, email, google_sub,
          is_admin, session_version, created_at
        FROM qb.account
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute("DROP INDEX IF EXISTS qb.account_email_uidx")
    op.execute("DROP INDEX IF EXISTS qb.account_google_sub_uidx")
    op.execute("ALTER TABLE qb.account DROP COLUMN IF EXISTS password_hash")
    op.execute("ALTER TABLE qb.account DROP COLUMN IF EXISTS email")
    op.execute("ALTER TABLE qb.account DROP COLUMN IF EXISTS google_sub")
    op.execute("ALTER TABLE qb.account DROP COLUMN IF EXISTS session_version")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE qb.account ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255)"
    )
    op.execute("ALTER TABLE qb.account ADD COLUMN IF NOT EXISTS email VARCHAR(320)")
    op.execute("ALTER TABLE qb.account ADD COLUMN IF NOT EXISTS google_sub VARCHAR(64)")
    op.execute(
        """
        ALTER TABLE qb.account
        ADD COLUMN IF NOT EXISTS session_version INTEGER NOT NULL DEFAULT 0
        """
    )
    op.execute(
        """
        UPDATE qb.account AS q
        SET
          password_hash = a.password_hash,
          email = a.email,
          google_sub = a.google_sub,
          session_version = a.session_version,
          is_admin = a.is_admin,
          username = a.username
        FROM auth.account AS a
        WHERE q.id = a.id
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS account_email_uidx
          ON qb.account (email) WHERE email IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS account_google_sub_uidx
          ON qb.account (google_sub) WHERE google_sub IS NOT NULL
        """
    )
