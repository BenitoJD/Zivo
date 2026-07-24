"""Google OAuth identity columns on qb.account.

Revision ID: 030_google_oauth
Revises: 029_newspaper_brands
Create Date: 2026-07-24

Adds google_sub + email for OAuth accounts; password_hash becomes nullable so
Google-only users need no password. Password signup still requires a hash.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "030_google_oauth"
down_revision: Union[str, None] = "029_newspaper_brands"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE qb.account ALTER COLUMN password_hash DROP NOT NULL")
    op.execute("ALTER TABLE qb.account ADD COLUMN IF NOT EXISTS email VARCHAR(320)")
    op.execute("ALTER TABLE qb.account ADD COLUMN IF NOT EXISTS google_sub VARCHAR(64)")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS account_email_uidx
          ON qb.account (email)
          WHERE email IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS account_google_sub_uidx
          ON qb.account (google_sub)
          WHERE google_sub IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS qb.account_google_sub_uidx")
    op.execute("DROP INDEX IF EXISTS qb.account_email_uidx")
    op.execute("ALTER TABLE qb.account DROP COLUMN IF EXISTS google_sub")
    op.execute("ALTER TABLE qb.account DROP COLUMN IF EXISTS email")
    # Existing Google-only rows (NULL password) block restoring NOT NULL.
    op.execute(
        """
        UPDATE qb.account
           SET password_hash = '!'
         WHERE password_hash IS NULL
        """
    )
    op.execute("ALTER TABLE qb.account ALTER COLUMN password_hash SET NOT NULL")
