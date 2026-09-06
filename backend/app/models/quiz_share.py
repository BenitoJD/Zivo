"""Shared MCQ quiz sets — create, share via link, take, review results."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Integer, Text, text
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class McqQuizSet(Base):
    __tablename__ = "mcq_quiz_sets"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    creator_id: Mapped[uuid.UUID | None] = mapped_column(UUID, nullable=True)
    creator_name: Mapped[str] = mapped_column(Text, server_default="Anonymous")
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default="")
    share_slug: Mapped[str] = mapped_column(Text, unique=True)
    is_public: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), server_default=sa.func.now()
    )


class McqQuizQuestion(Base):
    __tablename__ = "mcq_quiz_questions"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    quiz_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID, sa.ForeignKey("mcq_quiz_sets.id", ondelete="CASCADE")
    )
    question_text: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON)  # [{text: "..."}, ...]
    correct_index: Mapped[int] = mapped_column(Integer)
    explanation: Mapped[str] = mapped_column(Text, server_default="")
    sort_order: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), server_default=sa.func.now()
    )


class McqQuizAttempt(Base):
    __tablename__ = "mcq_quiz_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    quiz_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID, sa.ForeignKey("mcq_quiz_sets.id", ondelete="CASCADE")
    )
    taker_name: Mapped[str] = mapped_column(Text, server_default="Anonymous")
    taker_email: Mapped[str] = mapped_column(Text, server_default="")
    score: Mapped[int] = mapped_column(Integer, server_default="0")
    total_questions: Mapped[int] = mapped_column(Integer, server_default="0")
    answers: Mapped[list] = mapped_column(JSON, server_default="[]")  # [{q_id, selected}]
    started_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), server_default=sa.func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)
