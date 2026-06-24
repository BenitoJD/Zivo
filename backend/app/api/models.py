"""LLM model catalog API (no secrets)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Account as User
from app.models.llm import LlmModel
from app.services.auth import require_admin, require_csrf
from app.services.llm_pool import is_llm_pool_enabled, set_llm_pool_enabled
from app.services.llm_registry import (
    list_admin_chat_models,
    list_public_models,
    provider_has_api_key,
    set_chat_model_enabled,
)

router = APIRouter()


class ModelCapabilitiesOut(BaseModel):
    text_input: bool
    image_input: bool
    text_output: bool
    streaming: bool
    tools: bool


class ModelOut(BaseModel):
    id: uuid.UUID
    slug: str
    display_name: str
    kind: str
    provider_slug: str
    provider_name: str
    litellm_model: str
    capabilities: ModelCapabilitiesOut
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    is_default: bool

    model_config = {"from_attributes": True}


class ModelAdminOut(ModelOut):
    is_enabled: bool
    provider_enabled: bool
    has_api_key: bool


class ModelListOut(BaseModel):
    models: list[ModelOut]
    default_chat_model_id: uuid.UUID | None = None
    default_embedding_model_id: uuid.UUID | None = None


class AdminModelListOut(BaseModel):
    models: list[ModelAdminOut]
    pool_enabled: bool


class ModelPatchIn(BaseModel):
    is_enabled: bool


class PoolPatchIn(BaseModel):
    pool_enabled: bool


class PoolSettingsOut(BaseModel):
    pool_enabled: bool
    env_default: bool = Field(description="Default from LLM_POOL_ENABLED env when no runtime override")


def _capabilities(model: LlmModel) -> ModelCapabilitiesOut:
    return ModelCapabilitiesOut(
        text_input=model.supports_text_input,
        image_input=model.supports_image_input,
        text_output=model.supports_text_output,
        streaming=model.supports_streaming,
        tools=model.supports_tools,
    )


def _to_out(model: LlmModel) -> ModelOut:
    return ModelOut(
        id=model.id,
        slug=model.slug,
        display_name=model.display_name,
        kind=model.kind,
        provider_slug=model.provider.slug,
        provider_name=model.provider.display_name,
        litellm_model=model.litellm_model,
        capabilities=_capabilities(model),
        max_input_tokens=model.max_input_tokens,
        max_output_tokens=model.max_output_tokens,
        is_default=model.is_default,
    )


def _to_admin_out(model: LlmModel) -> ModelAdminOut:
    return ModelAdminOut(
        **_to_out(model).model_dump(),
        is_enabled=model.is_enabled,
        provider_enabled=model.provider.is_enabled,
        has_api_key=provider_has_api_key(model.provider),
    )


@router.get("", response_model=ModelListOut)
def list_models(db: Session = Depends(get_db)) -> ModelListOut:
    models, default_chat_id, default_embed_id = list_public_models(db)
    return ModelListOut(
        models=[_to_out(m) for m in models],
        default_chat_model_id=default_chat_id,
        default_embedding_model_id=default_embed_id,
    )


@router.get("/admin", response_model=AdminModelListOut)
def list_admin_models(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminModelListOut:
    models = list_admin_chat_models(db)
    return AdminModelListOut(
        models=[_to_admin_out(m) for m in models],
        pool_enabled=is_llm_pool_enabled(),
    )


@router.get("/pool", response_model=PoolSettingsOut)
def get_pool_settings(_: User = Depends(require_admin)) -> PoolSettingsOut:
    settings = get_settings()
    return PoolSettingsOut(
        pool_enabled=is_llm_pool_enabled(),
        env_default=settings.llm_pool_enabled,
    )


@router.patch("/pool", response_model=PoolSettingsOut, dependencies=[Depends(require_csrf)])
def patch_pool_settings(
    body: PoolPatchIn,
    _: User = Depends(require_admin),
) -> PoolSettingsOut:
    settings = get_settings()
    set_llm_pool_enabled(body.pool_enabled)
    return PoolSettingsOut(
        pool_enabled=is_llm_pool_enabled(),
        env_default=settings.llm_pool_enabled,
    )


@router.patch("/{model_id}", response_model=ModelAdminOut, dependencies=[Depends(require_csrf)])
def patch_model(
    model_id: uuid.UUID,
    body: ModelPatchIn,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ModelAdminOut:
    model = set_chat_model_enabled(db, model_id, enabled=body.is_enabled)
    return _to_admin_out(model)
