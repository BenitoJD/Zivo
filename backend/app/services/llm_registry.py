"""LLM provider + model registry — resolve models and configure LiteLLM."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.config import Settings, get_settings
from app.models.llm import LlmModel, LlmModelKind, LlmProvider

# LiteLLM reads these env vars by convention (https://docs.litellm.ai/docs/providers)
_PREFIX_ENV_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "openai": (
        ("api_key", "OPENAI_API_KEY"),
        ("api_base_url", "OPENAI_API_BASE"),
    ),
    "gemini": (("api_key", "GEMINI_API_KEY"),),
    "anthropic": (("api_key", "ANTHROPIC_API_KEY"),),
    "vertex_ai": (("api_key", "VERTEXAI_API_KEY"),),
    "azure": (
        ("api_key", "AZURE_API_KEY"),
        ("api_base_url", "AZURE_API_BASE"),
    ),
}


@dataclass(frozen=True)
class ResolvedLlmModel:
    record: LlmModel
    provider: LlmProvider

    @property
    def litellm_model(self) -> str:
        return self.record.litellm_model


def _enabled_chat_query(db: Session):
    return (
        db.query(LlmModel)
        .join(LlmProvider)
        .options(joinedload(LlmModel.provider))
        .filter(
            LlmModel.kind == LlmModelKind.chat,
            LlmModel.is_enabled.is_(True),
            LlmProvider.is_enabled.is_(True),
        )
    )


def resolve_chat_model(
    db: Session,
    *,
    model_id: uuid.UUID | None = None,
    require_vision: bool = False,
) -> ResolvedLlmModel:
    if model_id is not None:
        model = (
            _enabled_chat_query(db)
            .filter(LlmModel.id == model_id)
            .first()
        )
        if not model:
            raise HTTPException(status_code=404, detail="Model not found")
        if require_vision and not model.supports_image_input:
            raise HTTPException(status_code=400, detail="Selected model does not support images")
        return ResolvedLlmModel(record=model, provider=model.provider)

    query = _enabled_chat_query(db)
    if require_vision:
        query = query.filter(LlmModel.supports_image_input.is_(True))

    default = query.filter(LlmModel.is_default.is_(True)).order_by(LlmModel.sort_order).first()
    if default:
        return ResolvedLlmModel(record=default, provider=default.provider)

    fallback = query.order_by(LlmModel.sort_order, LlmModel.display_name).first()
    if fallback:
        return ResolvedLlmModel(record=fallback, provider=fallback.provider)

    raise HTTPException(status_code=503, detail="No chat model configured")


def resolve_embedding_model(db: Session) -> ResolvedLlmModel:
    model = (
        db.query(LlmModel)
        .join(LlmProvider)
        .options(joinedload(LlmModel.provider))
        .filter(
            LlmModel.kind == LlmModelKind.embedding,
            LlmModel.is_enabled.is_(True),
            LlmProvider.is_enabled.is_(True),
        )
        .order_by(LlmModel.is_default.desc(), LlmModel.sort_order, LlmModel.display_name)
        .first()
    )
    if not model:
        raise HTTPException(status_code=503, detail="No embedding model configured")
    return ResolvedLlmModel(record=model, provider=model.provider)


def configure_litellm(provider: LlmProvider) -> None:
    """Push provider credentials into env vars LiteLLM reads at call time."""
    mappings = _PREFIX_ENV_FIELDS.get(provider.litellm_prefix, ())
    for field, env_key in mappings:
        value = getattr(provider, field, None) or provider.extra_env.get(env_key)
        if value:
            if field == "api_base_url":
                value = str(value).rstrip("/")
            os.environ[env_key] = str(value)

    for env_key, value in (provider.extra_env or {}).items():
        if value and env_key not in {m[1] for m in mappings}:
            os.environ[env_key] = str(value)


def list_public_models(db: Session) -> tuple[list[LlmModel], uuid.UUID | None, uuid.UUID | None]:
    models = (
        db.query(LlmModel)
        .join(LlmProvider)
        .options(joinedload(LlmModel.provider))
        .filter(LlmModel.is_enabled.is_(True), LlmProvider.is_enabled.is_(True))
        .order_by(LlmModel.kind, LlmModel.sort_order, LlmModel.display_name)
        .all()
    )
    default_chat_id = next((m.id for m in models if m.kind == LlmModelKind.chat and m.is_default), None)
    default_embed_id = next((m.id for m in models if m.kind == LlmModelKind.embedding and m.is_default), None)
    return models, default_chat_id, default_embed_id


def _provider_slug_for_litellm_model(litellm_model: str) -> str:
    prefix = litellm_model.split("/", 1)[0]
    return {
        "openai": "openai",
        "gemini": "gemini",
        "anthropic": "anthropic",
    }.get(prefix, prefix)


def _upsert_provider(
    db: Session,
    *,
    slug: str,
    display_name: str,
    litellm_prefix: str,
    api_base_url: str | None = None,
    api_key: str | None = None,
) -> LlmProvider:
    provider = db.query(LlmProvider).filter(LlmProvider.slug == slug).first()
    if provider:
        if api_base_url:
            provider.api_base_url = api_base_url
        if api_key:
            provider.api_key = api_key
        provider.is_enabled = True
        return provider

    provider = LlmProvider(
        slug=slug,
        display_name=display_name,
        litellm_prefix=litellm_prefix,
        api_base_url=api_base_url,
        api_key=api_key,
    )
    db.add(provider)
    db.flush()
    return provider


def _upsert_model(
    db: Session,
    *,
    provider: LlmProvider,
    slug: str,
    litellm_model: str,
    display_name: str,
    kind: LlmModelKind,
    is_default: bool = False,
    supports_image_input: bool = False,
    supports_streaming: bool = True,
    max_input_tokens: int | None = None,
    max_output_tokens: int | None = None,
    sort_order: int = 0,
    meta: dict | None = None,
) -> LlmModel:
    model = (
        db.query(LlmModel)
        .filter(LlmModel.provider_id == provider.id, LlmModel.slug == slug)
        .first()
    )
    if model:
        model.is_default = is_default
        model.is_enabled = True
        return model

    model = LlmModel(
        provider_id=provider.id,
        slug=slug,
        litellm_model=litellm_model,
        display_name=display_name,
        kind=kind.value,
        is_default=is_default,
        supports_image_input=supports_image_input,
        supports_streaming=supports_streaming,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        sort_order=sort_order,
        meta=meta or {},
    )
    db.add(model)
    return model


def _clear_defaults_for_kind(db: Session, kind: LlmModelKind) -> None:
    db.query(LlmModel).filter(LlmModel.kind == kind.value, LlmModel.is_default.is_(True)).update(
        {"is_default": False},
        synchronize_session=False,
    )


def refresh_llm_registry_from_env(
    db: Session,
    settings: Settings | None = None,
    *,
    force_keys: bool = False,
) -> bool:
    """Apply env API keys/base URLs and default chat model. Returns True if DB changed."""
    settings = settings or get_settings()
    dirty = False

    if settings.openai_api_key or settings.openai_api_base:
        provider = db.query(LlmProvider).filter(LlmProvider.slug == "openai").first()
        if provider:
            if settings.openai_api_key and (force_keys or not provider.api_key):
                provider.api_key = settings.openai_api_key
                dirty = True
            if settings.openai_api_base and (force_keys or not provider.api_base_url):
                provider.api_base_url = settings.openai_api_base.rstrip("/")
                dirty = True
    if settings.gemini_api_key:
        provider = db.query(LlmProvider).filter(LlmProvider.slug == "gemini").first()
        if provider and (force_keys or not provider.api_key):
            provider.api_key = settings.gemini_api_key
            dirty = True
    if settings.anthropic_api_key:
        provider = db.query(LlmProvider).filter(LlmProvider.slug == "anthropic").first()
        if provider and (force_keys or not provider.api_key):
            provider.api_key = settings.anthropic_api_key
            dirty = True

    if sync_default_chat_model_from_env(db, settings):
        dirty = True

    if dirty:
        db.commit()
    return dirty


def sync_default_chat_model_from_env(db: Session, settings: Settings | None = None) -> bool:
    """Point the default chat model at ``settings.litellm_model`` when that row exists."""
    settings = settings or get_settings()
    litellm_model = settings.litellm_model.strip()
    if not litellm_model or "/" not in litellm_model:
        return False

    target_slug = _provider_slug_for_litellm_model(litellm_model)
    provider = db.query(LlmProvider).filter(LlmProvider.slug == target_slug).first()
    if not provider:
        return False

    model = (
        db.query(LlmModel)
        .filter(LlmModel.provider_id == provider.id, LlmModel.litellm_model == litellm_model)
        .first()
    )
    if not model:
        slug = litellm_model.split("/", 1)[-1].replace("/", "-")
        model = (
            db.query(LlmModel)
            .filter(LlmModel.provider_id == provider.id, LlmModel.slug == slug)
            .first()
        )
    if not model or not model.is_enabled:
        return False

    current_default = (
        db.query(LlmModel)
        .filter(LlmModel.kind == LlmModelKind.chat, LlmModel.is_default.is_(True))
        .first()
    )
    if current_default and current_default.id == model.id:
        return False

    _clear_defaults_for_kind(db, LlmModelKind.chat)
    model.is_default = True
    return True


def bootstrap_llm_registry_from_env(db: Session, settings: Settings | None = None) -> None:
    """Seed or refresh providers/models from env on startup (idempotent)."""
    settings = settings or get_settings()

    if db.query(LlmProvider).count() == 0:
        local = _upsert_provider(
            db,
            slug="local",
            display_name="Local",
            litellm_prefix="local",
        )
        _upsert_model(
            db,
            provider=local,
            slug="minilm-l6",
            litellm_model=settings.embed_model,
            display_name="MiniLM L6 (embeddings)",
            kind=LlmModelKind.embedding,
            is_default=True,
            supports_streaming=False,
            meta={"embed_dimension": settings.embed_dimension},
            sort_order=0,
        )

        openai = _upsert_provider(
            db,
            slug="openai",
            display_name="OpenAI-compatible",
            litellm_prefix="openai",
            api_base_url=settings.openai_api_base or None,
            api_key=settings.openai_api_key or None,
        )
        _upsert_model(
            db,
            provider=openai,
            slug="mimo-v2.5",
            litellm_model="openai/mimo-v2.5",
            display_name="MiMo v2.5",
            kind=LlmModelKind.chat,
            is_default=settings.litellm_model == "openai/mimo-v2.5",
            sort_order=10,
            max_input_tokens=128_000,
            max_output_tokens=8_192,
        )

        gemini = _upsert_provider(
            db,
            slug="gemini",
            display_name="Google Gemini",
            litellm_prefix="gemini",
            api_key=settings.gemini_api_key or None,
        )
        _upsert_model(
            db,
            provider=gemini,
            slug="gemini-2.0-flash",
            litellm_model="gemini/gemini-2.0-flash",
            display_name="Gemini 2.0 Flash",
            kind=LlmModelKind.chat,
            is_default=settings.litellm_model == "gemini/gemini-2.0-flash",
            supports_image_input=True,
            sort_order=20,
            max_input_tokens=1_048_576,
            max_output_tokens=8_192,
        )

        anthropic = _upsert_provider(
            db,
            slug="anthropic",
            display_name="Anthropic",
            litellm_prefix="anthropic",
            api_key=settings.anthropic_api_key or None,
        )
        _upsert_model(
            db,
            provider=anthropic,
            slug="claude-3-5-sonnet",
            litellm_model="anthropic/claude-3-5-sonnet-20241022",
            display_name="Claude 3.5 Sonnet",
            kind=LlmModelKind.chat,
            is_default=settings.litellm_model.startswith("anthropic/"),
            supports_image_input=True,
            sort_order=30,
            max_input_tokens=200_000,
            max_output_tokens=8_192,
        )

        if not db.query(LlmModel).filter(LlmModel.kind == LlmModelKind.chat, LlmModel.is_default.is_(True)).count():
            _clear_defaults_for_kind(db, LlmModelKind.chat)
            target_slug = _provider_slug_for_litellm_model(settings.litellm_model)
            model_name = settings.litellm_model.split("/", 1)[-1]
            provider = db.query(LlmProvider).filter(LlmProvider.slug == target_slug).first()
            if provider:
                custom = _upsert_model(
                    db,
                    provider=provider,
                    slug=model_name.replace("/", "-"),
                    litellm_model=settings.litellm_model,
                    display_name=model_name,
                    kind=LlmModelKind.chat,
                    is_default=True,
                    supports_image_input=target_slug in {"gemini", "anthropic", "openai"},
                    sort_order=5,
                )
                custom.is_default = True

        db.commit()
        return

    refresh_llm_registry_from_env(db, settings, force_keys=False)


def seed_llm_registry_from_env(db: Session, settings: Settings | None = None, *, force_keys: bool = True) -> None:
    """Dev seed: ensure registry rows exist and env keys/default model are applied."""
    settings = settings or get_settings()
    if db.query(LlmProvider).count() == 0:
        bootstrap_llm_registry_from_env(db, settings)
    else:
        refresh_llm_registry_from_env(db, settings, force_keys=force_keys)
