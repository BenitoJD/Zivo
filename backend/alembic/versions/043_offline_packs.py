"""Server-tracked Offline Mode study packs (ADR 0006).

A pack is a signed, expiring snapshot of an artifact's full active question
pool *with answer keys + pre-baked feedback* plus the learner's mastery
snapshot, downloadable for offline study. The pack_payload JSONB is NULL
while the build ETA job runs (pending -> building -> ready | failed) and is
filled once the deck is assembled + option feedback is backfilled.

Additive: the table is net-new in qb.* (ADR 0002), all columns default safely,
so a running pod that predates this migration simply never writes here.

Revision ID: 043_offline_packs
Revises: 042_job_lease_heartbeat
"""

from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "043_offline_packs"
down_revision: Union[str, None] = "042_job_lease_heartbeat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS qb.offline_packs (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          document_id     UUID NOT NULL REFERENCES qb.documents (id) ON DELETE CASCADE,
          account_id      UUID,
          guest_id        TEXT,
          status          TEXT NOT NULL DEFAULT 'pending',
          progress        NUMERIC(5,2) NOT NULL DEFAULT 0,
          question_count  INTEGER NOT NULL DEFAULT 0,
          pack_payload    JSONB,
          signature       TEXT,
          expires_at      TIMESTAMPTZ NOT NULL,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          error           TEXT,
          CONSTRAINT offline_packs_owner_xor_check
            CHECK ((account_id IS NULL) <> (guest_id IS NULL))
        )
        """
    )
    # Lookup a learner's pack for a source ("already downloaded?" badge) and
    # ownership scoping on every read. Partial — only built/ready packs matter.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS offline_packs_doc_owner_idx
        ON qb.offline_packs (document_id, account_id, guest_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS qb.offline_packs_doc_owner_idx")
    op.execute("DROP TABLE IF EXISTS qb.offline_packs")
