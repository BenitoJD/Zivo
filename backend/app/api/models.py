"""LLM model catalog API (no secrets)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.llm import LlmModel
from app.services.llm_registry import list_public_models

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


class ModelListOut(BaseModel):
    models: list[ModelOut]
    default_chat_model_id: uuid.UUID | None = None
    default_embedding_model_id: uuid.UUID | None = None


def _to_out(model: LlmModel) -> ModelOut:
    return ModelOut(
        id=model.id,
        slug=model.slug,
        display_name=model.display_name,
        kind=model.kind,
        provider_slug=model.provider.slug,
        provider_name=model.provider.display_name,
        litellm_model=model.litellm_model,
        capabilities=ModelCapabilitiesOut(
            text_input=model.supports_text_input,
            image_input=model.supports_image_input,
            text_output=model.supports_text_output,
            streaming=model.supports_streaming,
            tools=model.supports_tools,
        ),
        max_input_tokens=model.max_input_tokens,
        max_output_tokens=model.max_output_tokens,
        is_default=model.is_default,
    )


@router.get("", response_model=ModelListOut)
def list_models(db: Session = Depends(get_db)) -> ModelListOut:
    models, default_chat_id, default_embed_id = list_public_models(db)
    return ModelListOut(
        models=[_to_out(m) for m in models],
        default_chat_model_id=default_chat_id,
        default_embedding_model_id=default_embed_id,
    )
