"""LLM provider + model registry — resolve models.

Provider credentials are NOT pushed into os.environ here. litellm_provider_kwargs
passes api_key/api_base explicitly to every litellm.acompletion call, so a shared
os.environ would only race between concurrent jobs using different providers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.config import Settings, get_settings
from app.models.llm import LlmModel, LlmModelKind, LlmProvider


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


def default_chat_model_id(db: Session) -> uuid.UUID | None:
    """The pinned default chat model id, if any.

    Used by background generation to pin every call to one model instead of
    round-robining the pool. Round-robin spreads a parallel batch across models
    of different speed, so the batch always waits on the slowest. Pinning to the
    default (the fastest/quality default) collapses batch wall-clock and removes
    cross-model stragglers. Interactive chat keeps using the pool for failover.
    """
    model = (
        _enabled_chat_query(db)
        .order_by(LlmModel.is_default.desc(), LlmModel.sort_order, LlmModel.display_name)
        .first()
    )
    return model.id if model else None


def list_admin_chat_models(db: Session) -> list[LlmModel]:
    return (
        db.query(LlmModel)
        .join(LlmProvider)
        .options(joinedload(LlmModel.provider))
        .filter(LlmModel.kind == LlmModelKind.chat)
        .order_by(LlmProvider.display_name, LlmModel.sort_order, LlmModel.display_name)
        .all()
    )


def provider_has_api_key(provider: LlmProvider) -> bool:
    if provider.slug == "local":
        return True
    return bool((provider.api_key or "").strip())


def set_chat_model_enabled(db: Session, model_id: uuid.UUID, *, enabled: bool) -> LlmModel:
    model = (
        db.query(LlmModel)
        .options(joinedload(LlmModel.provider))
        .filter(LlmModel.id == model_id, LlmModel.kind == LlmModelKind.chat)
        .first()
    )
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    model.is_enabled = enabled
    if not enabled and model.is_default:
        model.is_default = False
        replacement = (
            _enabled_chat_query(db)
            .filter(LlmModel.id != model_id)
            .order_by(LlmModel.sort_order, LlmModel.display_name)
            .first()
        )
        if replacement:
            _clear_defaults_for_kind(db, LlmModelKind.chat)
            replacement.is_default = True

    db.commit()
    db.refresh(model)
    return model


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


# GLM 4.x models default to a reasoning/"thinking" mode that emits a 1–3k-token
# trace we discard (only the small MCQ JSON block is parsed). Disabling it is a
# ~5× latency / ~14× token reduction for generation. See llm_pool.litellm_provider_kwargs.
GLM_THINKING_DISABLED_META = {
    "thinking_disabled": True,
    "max_tokens": 600,
}


def _provider_slug_for_litellm_model(litellm_model: str) -> str:
    prefix = litellm_model.split("/", 1)[0]
    return {
        "openai": "openai",
        "openrouter": "openrouter",
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
    update_fields: bool = False,
    is_enabled: bool | None = None,
) -> LlmModel:
    model = (
        db.query(LlmModel)
        .filter(LlmModel.provider_id == provider.id, LlmModel.slug == slug)
        .first()
    )
    if model:
        if update_fields:
            model.litellm_model = litellm_model
            model.display_name = display_name
            model.sort_order = sort_order
            if max_input_tokens is not None:
                model.max_input_tokens = max_input_tokens
            if max_output_tokens is not None:
                model.max_output_tokens = max_output_tokens
            if meta is not None:
                model.meta = meta
        if is_default:
            model.is_default = True
        return model

    model = LlmModel(
        provider_id=provider.id,
        slug=slug,
        litellm_model=litellm_model,
        display_name=display_name,
        kind=kind.value,
        is_enabled=is_enabled if is_enabled is not None else True,
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


def ensure_registry_providers(db: Session, settings: Settings | None = None) -> bool:
    """Upsert known providers/models so existing DBs pick up new routes (e.g. Z.AI GLM)."""
    settings = settings or get_settings()
    dirty = False

    zai = _upsert_provider(
        db,
        slug="zai",
        display_name="Z.AI",
        litellm_prefix="openai",
        api_base_url=settings.zai_api_base.rstrip("/") or None,
        api_key=settings.zai_api_key or None,
    )
    before = (
        db.query(LlmModel)
        .filter(LlmModel.provider_id == zai.id, LlmModel.slug == "glm-4.7")
        .count()
    )
    _upsert_model(
        db,
        provider=zai,
        slug="glm-4.7",
        litellm_model="openai/glm-4.7",
        display_name="GLM 4.7",
        kind=LlmModelKind.chat,
        sort_order=15,
        max_input_tokens=200_000,
        max_output_tokens=8_192,
        update_fields=True,
        meta=GLM_THINKING_DISABLED_META,
    )
    # Force the thinking-disabled meta onto pre-existing GLM rows whose meta was
    # previously empty, so live DBs pick up the latency fix on next boot.
    glm = (
        db.query(LlmModel)
        .filter(LlmModel.provider_id == zai.id, LlmModel.slug == "glm-4.7")
        .first()
    )
    if glm and not (glm.meta or {}).get("thinking_disabled"):
        glm.meta = GLM_THINKING_DISABLED_META
        dirty = True
    if before == 0:
        dirty = True

    stepfun = _upsert_provider(
        db,
        slug="stepfun",
        display_name="Step Fun",
        litellm_prefix="openai",
        api_base_url=settings.stepfun_api_base.rstrip("/") or None,
        api_key=settings.stepfun_api_key or None,
    )
    before = (
        db.query(LlmModel)
        .filter(LlmModel.provider_id == stepfun.id, LlmModel.slug == "step-3.5-flash")
        .count()
    )
    _upsert_model(
        db,
        provider=stepfun,
        slug="step-3.5-flash",
        litellm_model="openai/step-3.5-flash",
        display_name="Step 3.5 Flash",
        kind=LlmModelKind.chat,
        sort_order=5,
        max_input_tokens=128_000,
        max_output_tokens=8_192,
        update_fields=True,
    )
    if before == 0:
        dirty = True
    # When a Step Fun key is configured, pin step-3.5-flash as the default chat
    # model (and thus the pinned generation model) on existing DBs too. This
    # runs on every boot, so it tracks key presence without a manual admin step.
    # Note: no thinking_disabled meta — Step Fun's reasoning is already cheap
    # (~5-80 tokens), and the thinking={"type":"disabled"} param is not cleanly
    # honored (empirically it inflated tokens). Tested baseline latency ~1.4-2.3s.
    stepfun_model = (
        db.query(LlmModel)
        .filter(LlmModel.provider_id == stepfun.id, LlmModel.slug == "step-3.5-flash")
        .first()
    )
    if settings.stepfun_api_key and stepfun_model and not stepfun_model.is_default:
        _clear_defaults_for_kind(db, LlmModelKind.chat)
        stepfun_model.is_default = True
        dirty = True

    openrouter = _upsert_provider(
        db,
        slug="openrouter",
        display_name="OpenRouter",
        litellm_prefix="openrouter",
        api_base_url=settings.openrouter_api_base.rstrip("/") or None,
        api_key=settings.openrouter_api_key or None,
    )
    before = (
        db.query(LlmModel)
        .filter(LlmModel.provider_id == openrouter.id, LlmModel.slug == "free")
        .count()
    )
    _upsert_model(
        db,
        provider=openrouter,
        slug="free",
        litellm_model="openrouter/openrouter/free",
        display_name="OpenRouter Free",
        kind=LlmModelKind.chat,
        sort_order=12,
        max_input_tokens=128_000,
        max_output_tokens=8_192,
        update_fields=True,
    )
    if before == 0:
        dirty = True

    openai = db.query(LlmProvider).filter(LlmProvider.slug == "openai").first()
    if openai is None and (settings.openai_api_key or settings.openai_api_base):
        openai = _upsert_provider(
            db,
            slug="openai",
            display_name="OpenAI-compatible",
            litellm_prefix="openai",
            api_base_url=settings.openai_api_base or None,
            api_key=settings.openai_api_key or None,
        )
        dirty = True
    if openai:
        before = (
            db.query(LlmModel)
            .filter(LlmModel.provider_id == openai.id, LlmModel.slug == "mimo-v2.5")
            .count()
        )
        _upsert_model(
            db,
            provider=openai,
            slug="mimo-v2.5",
            litellm_model="openai/mimo-v2.5",
            display_name="MiMo v2.5",
            kind=LlmModelKind.chat,
            sort_order=10,
            max_input_tokens=128_000,
            max_output_tokens=8_192,
            update_fields=True,
            is_enabled=False,
        )
        if before == 0:
            dirty = True

    return dirty


def refresh_llm_registry_from_env(
    db: Session,
    settings: Settings | None = None,
    *,
    force_keys: bool = False,
) -> bool:
    """Apply env API keys/base URLs and default chat model. Returns True if DB changed."""
    settings = settings or get_settings()
    dirty = False

    if ensure_registry_providers(db, settings):
        dirty = True

    if settings.zai_api_key or settings.zai_api_base:
        provider = db.query(LlmProvider).filter(LlmProvider.slug == "zai").first()
        if provider:
            if settings.zai_api_key and (force_keys or not provider.api_key):
                provider.api_key = settings.zai_api_key
                dirty = True
            if settings.zai_api_base and (force_keys or not provider.api_base_url):
                provider.api_base_url = settings.zai_api_base.rstrip("/")
                dirty = True

    if settings.stepfun_api_key or settings.stepfun_api_base:
        provider = db.query(LlmProvider).filter(LlmProvider.slug == "stepfun").first()
        if provider:
            if settings.stepfun_api_key and (force_keys or not provider.api_key):
                provider.api_key = settings.stepfun_api_key
                dirty = True
            if settings.stepfun_api_base and (force_keys or not provider.api_base_url):
                provider.api_base_url = settings.stepfun_api_base.rstrip("/")
                dirty = True

    if settings.openrouter_api_key or settings.openrouter_api_base:
        provider = db.query(LlmProvider).filter(LlmProvider.slug == "openrouter").first()
        if provider:
            if settings.openrouter_api_key and (force_keys or not provider.api_key):
                provider.api_key = settings.openrouter_api_key
                dirty = True
            if settings.openrouter_api_base and (force_keys or not provider.api_base_url):
                provider.api_base_url = settings.openrouter_api_base.rstrip("/")
                dirty = True

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

    # Self-healing: disable any enabled chat model whose provider has no API key.
    # Prevents keyless models (e.g. Gemini/Anthropic when no key is configured)
    # from sitting enabled in the pool, where they'd only ever fail. A model is
    # re-enabled the moment its provider gets a key (the blocks above set it).
    for model in (
        db.query(LlmModel)
        .join(LlmProvider)
        .filter(LlmModel.kind == LlmModelKind.chat, LlmModel.is_enabled.is_(True))
        .all()
    ):
        if not _provider_has_api_key(model.provider) and model.provider.slug != "local":
            model.is_enabled = False
            if model.is_default:
                model.is_default = False
            dirty = True

    if sync_default_chat_model_from_env(db, settings):
        dirty = True

    if sync_default_embedding_model_from_env(db, settings):
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


def sync_default_embedding_model_from_env(db: Session, settings: Settings | None = None) -> bool:
    """Keep the default embedding row aligned with ``settings.embed_model``."""
    settings = settings or get_settings()
    embed_model = settings.embed_model.strip()
    if not embed_model:
        return False

    model = (
        db.query(LlmModel)
        .filter(LlmModel.kind == LlmModelKind.embedding, LlmModel.is_default.is_(True))
        .first()
    )
    if not model:
        return False

    dirty = False
    if model.litellm_model != embed_model:
        model.litellm_model = embed_model
        dirty = True
    display_name = "BGE small en v1.5 (embeddings)"
    if model.display_name != display_name:
        model.display_name = display_name
        dirty = True
    meta = dict(model.meta or {})
    if meta.get("embed_dimension") != settings.embed_dimension:
        meta["embed_dimension"] = settings.embed_dimension
        model.meta = meta
        dirty = True
    return dirty


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
            slug="bge-small-en",
            litellm_model=settings.embed_model,
            display_name="BGE small en v1.5 (embeddings)",
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
            is_default=False,
            is_enabled=False,
            sort_order=10,
            max_input_tokens=128_000,
            max_output_tokens=8_192,
        )

        zai = _upsert_provider(
            db,
            slug="zai",
            display_name="Z.AI",
            litellm_prefix="openai",
            api_base_url=settings.zai_api_base.rstrip("/") or None,
            api_key=settings.zai_api_key or None,
        )
        _upsert_model(
            db,
            provider=zai,
            slug="glm-4.7",
            litellm_model="openai/glm-4.7",
            display_name="GLM 4.7",
            kind=LlmModelKind.chat,
            is_default=settings.litellm_model == "openai/glm-4.7",
            sort_order=15,
            max_input_tokens=200_000,
            max_output_tokens=8_192,
            meta=GLM_THINKING_DISABLED_META,
        )

        stepfun = _upsert_provider(
            db,
            slug="stepfun",
            display_name="Step Fun",
            litellm_prefix="openai",
            api_base_url=settings.stepfun_api_base.rstrip("/") or None,
            api_key=settings.stepfun_api_key or None,
        )
        # step-3.5-flash becomes the pinned default generation model when a key
        # is configured — fast, ideal for high-volume MCQ cook.
        stepfun_is_default = bool(settings.stepfun_api_key)
        _upsert_model(
            db,
            provider=stepfun,
            slug="step-3.5-flash",
            litellm_model="openai/step-3.5-flash",
            display_name="Step 3.5 Flash",
            kind=LlmModelKind.chat,
            is_default=stepfun_is_default,
            sort_order=5,
            max_input_tokens=128_000,
            max_output_tokens=8_192,
        )

        openrouter = _upsert_provider(
            db,
            slug="openrouter",
            display_name="OpenRouter",
            litellm_prefix="openrouter",
            api_base_url=settings.openrouter_api_base.rstrip("/") or None,
            api_key=settings.openrouter_api_key or None,
        )
        _upsert_model(
            db,
            provider=openrouter,
            slug="free",
            litellm_model="openrouter/openrouter/free",
            display_name="OpenRouter Free",
            kind=LlmModelKind.chat,
            is_default=settings.litellm_model == "openrouter/openrouter/free",
            sort_order=12,
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
        # Only enable when a key is configured — a keyless model in the pool
        # only ever fails and adds noise to the admin UI.
        _upsert_model(
            db,
            provider=gemini,
            slug="gemini-2.0-flash",
            litellm_model="gemini/gemini-2.0-flash",
            display_name="Gemini 2.0 Flash",
            kind=LlmModelKind.chat,
            is_enabled=bool(settings.gemini_api_key),
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
            is_enabled=bool(settings.anthropic_api_key),
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

    refresh_llm_registry_from_env(db, settings, force_keys=True)


def seed_llm_registry_from_env(db: Session, settings: Settings | None = None, *, force_keys: bool = True) -> None:
    """Dev seed: ensure registry rows exist and env keys/default model are applied."""
    settings = settings or get_settings()
    if db.query(LlmProvider).count() == 0:
        bootstrap_llm_registry_from_env(db, settings)
    else:
        refresh_llm_registry_from_env(db, settings, force_keys=force_keys)
