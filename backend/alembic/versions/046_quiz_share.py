"""shared MCQ quiz sets with shareable links, attempts, and LLM-powered generation

Revision ID: 046_quiz_share
Revises: 045_auth_account_split
Create Date: 2026-09-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "046_quiz_share"
down_revision = "045_auth_account_split"


def upgrade() -> None:
    op.create_table(
        "mcq_quiz_sets",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creator_id", UUID, nullable=True),
        sa.Column("creator_name", sa.Text, nullable=False, server_default="Anonymous"),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("share_slug", sa.Text, unique=True, nullable=False),
        sa.Column("is_public", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_mcq_quiz_sets_slug", "mcq_quiz_sets", ["share_slug"], unique=True)

    op.create_table(
        "mcq_quiz_questions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("quiz_set_id", UUID, sa.ForeignKey("mcq_quiz_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_text", sa.Text, nullable=False),
        sa.Column("options", sa.JSON, nullable=False),
        sa.Column("correct_index", sa.Integer, nullable=False),
        sa.Column("explanation", sa.Text, server_default=""),
        sa.Column("sort_order", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_mcq_quiz_questions_set", "mcq_quiz_questions", ["quiz_set_id"])

    op.create_table(
        "mcq_quiz_attempts",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("quiz_set_id", UUID, sa.ForeignKey("mcq_quiz_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("taker_name", sa.Text, server_default="Anonymous"),
        sa.Column("taker_email", sa.Text, server_default=""),
        sa.Column("score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_questions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("answers", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
    )
    op.create_index("ix_mcq_quiz_attempts_set", "mcq_quiz_attempts", ["quiz_set_id"])


def downgrade() -> None:
    op.drop_table("mcq_quiz_attempts")
    op.drop_table("mcq_quiz_questions")
    op.drop_table("mcq_quiz_sets")
