"""Add a partial expression index on qb.documents (meta->>'guest_id') for
anonymous-user lookups.

The anonymous (guest) hot path filters on `account_id IS NULL AND
meta->>'guest_id' = ?` in five call sites (guest_document_count,
guest_storage_used, assert_storage_available guest branch, list_documents guest
branch, claim_guest_documents). The existing indexes are on `account_id` and
`slug`, neither usable for a NULL-account + JSON-text filter, so every guest
page load / upload full-scanned all anonymous documents — which grows
unbounded over time.

A partial expression index scoped to `WHERE account_id IS NULL` turns all five
into cheap index lookups while staying small (only guest rows are indexed).

Revision ID: 005_guest_document_idx
Revises: 004_assertion_payload_lookup_idx
Create Date: 2026-06-24
"""

from typing import Sequence, Union

from alembic import op

revision: str = "005_guest_document_idx"
down_revision: Union[str, None] = "004_assertion_payload_lookup_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS documents_guest_id_idx
            ON qb.documents ((meta->>'guest_id'))
            WHERE account_id IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS qb.documents_guest_id_idx")
