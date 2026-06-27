"""Idempotent answer measurements: one immutable row per (learner, item, metric).

Revision ID: 013_measurement_answer_idempotent
Revises: 012_practice_library
Create Date: 2026-06-27

Without this, a client retry or a worker crash + replay could insert duplicate
answer rows into intel.measurement and double-count calibration. The partial unique
index lets the answer write use ON CONFLICT DO NOTHING so the second write is a no-op.
It is scoped to rows that carry both a learner (subject_entity_id) and an item
(source_assertion_id) — i.e. answer events — leaving other measurement series
(prices, weather, …) unconstrained.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "013_measurement_answer_idempotent"
down_revision: Union[str, None] = "012_practice_library"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS measurement_answer_idempotent
        ON intel.measurement (subject_entity_id, source_assertion_id, metric_concept_id)
        WHERE subject_entity_id IS NOT NULL AND source_assertion_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS intel.measurement_answer_idempotent")
