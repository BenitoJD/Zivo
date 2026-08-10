"""LLM provider and model registry."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class LlmModelKind(StrEnum):
    chat = "chat"
    embedding = "embedding"


class LlmProvider(Base):
    __tablename__ = "llm_providers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    litellm_prefix: Mapped[str] = mapped_column(String(64))
    api_base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_env: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Max concurrent in-flight calls this provider tolerates. NULL = no
    # provider-specific limit (only the process-wide LLM_MAX_CONCURRENT applies).
    # Step Fun rejects the 9th concurrent call outright, and a global env cap
    # can't express "8 here, more there" — so the limit lives with the provider.
    max_concurrency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    models: Mapped[list["LlmModel"]] = relationship(back_populates="provider")


class LlmModel(Base):
    __tablename__ = "llm_models"
    __table_args__ = (UniqueConstraint("provider_id", "slug", name="uq_llm_models_provider_slug"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("llm_providers.id"), index=True)
    slug: Mapped[str] = mapped_column(String(128))
    litellm_model: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32), default=LlmModelKind.chat, index=True)
    supports_text_input: Mapped[bool] = mapped_column(Boolean, default=True)
    supports_image_input: Mapped[bool] = mapped_column(Boolean, default=False)
    # Vision-only: selectable ONLY for require_vision calls (OCR/images), kept out of
    # the text pool so a metered vision model never serves ordinary text or failover.
    vision_only: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_text_output: Mapped[bool] = mapped_column(Boolean, default=True)
    supports_streaming: Mapped[bool] = mapped_column(Boolean, default=True)
    supports_tools: Mapped[bool] = mapped_column(Boolean, default=False)
    max_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    provider: Mapped[LlmProvider] = relationship(back_populates="models")
