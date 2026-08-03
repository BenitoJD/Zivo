"""LLM provider + model registry — resolve models and configure LiteLLM."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session, joinedload

from app.config import Settings, get_settings
from app.models.llm import LlmModel, LlmModelKind, LlmProvider
from app.services.token_budget import MIN_MODEL_META_MAX_TOKENS

# LiteLLM reads these env vars by convention (https://docs.litellm.ai/docs/providers)
_PREFIX_ENV_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "openai": (
        ("api_key", "OPENAI_API_KEY"),
        ("api_base_url", "OPENAI_API_BASE"),
    ),
    "openrouter": (
        ("api_key", "OPENROUTER_API_KEY"),
        ("api_base_url", "OPENROUTER_API_BASE"),
    ),
    "gemini": (("api_key", "GEMINI_API_KEY"),),
    "anthropic": (("api_key", "ANTHROPIC_API_KEY"),),
    "vertex_ai": (("api_key", "VERTEXAI_API_KEY"),),
    "azure": (
        ("api_key", "AZURE_API_KEY"),
        ("api_base_url", "AZURE_API_BASE"),
    ),
}

# Stable advisory-lock key for registry mutations. Two pods can boot at once
# (rolling update) and both run ensure_registry_providers + sync_default_*_model:
# each clears the existing default then sets a new one within one transaction, so
# without serialization the two flushes together emit two is_default=True rows and
# trip uq_llm_models_default_per_kind, aborting the whole bootstrap. The
# transaction-scoped lock (pg_advisory_xact_lock) makes one pod's clear+set+commit
# atomic relative to the other's; it auto-releases on commit/rollback. Fixed key so
# every concurrent boot contends on the same lock.
_REGISTRY_LOCK_KEY = 0x5A1C0  # registry default-mutation advisory-lock key


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
    else:
        # Vision-only models are never a text default/fallback.
        query = query.filter(LlmModel.vision_only.is_(False))

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
        .filter(LlmModel.vision_only.is_(False))  # never pin text generation to a vision-only model
        .order_by(LlmModel.is_default.desc(), LlmModel.sort_order, LlmModel.display_name)
        .first()
    )
    return model.id if model else None


def draft_chat_model_id(db: Session) -> uuid.UUID | None:
    """The model to use for writing DRAFTS, if a separate draft model is configured.

    Lets ops point drafting at a faster/cheaper model (latency lever) while the
    critic + answer-key verifier keep using the strong default. Returns None when
    `draft_model_name` is unset or doesn't match an enabled chat model, so callers
    fall back to `default_chat_model_id`.
    """
    name = (get_settings().draft_model_name or "").strip()
    if not name:
        return None
    model = (
        _enabled_chat_query(db)
        .filter(func.lower(LlmModel.display_name) == name.lower())
        .filter(LlmModel.vision_only.is_(False))  # drafting is text — never a vision-only model
        .order_by(LlmModel.sort_order)
        .first()
    )
    return model.id if model else None


def vision_chat_model_id(db: Session) -> uuid.UUID | None:
    """The vision model to use for reading answer images (Mains OCR), if pinned.

    Plug-and-play: set `vision_model_name` (env/settings) to any enabled
    vision-capable model's display name to pin OCR to it. Returns None when unset
    or unmatched, so callers fall back to `require_vision=True` auto-routing (the
    enabled default vision model). Mirrors `draft_model_name`.
    """
    name = (get_settings().vision_model_name or "").strip()
    if not name:
        return None
    model = (
        _enabled_chat_query(db)
        .filter(
            func.lower(LlmModel.display_name) == name.lower(),
            LlmModel.supports_image_input.is_(True),
        )
        .order_by(LlmModel.sort_order)
        .first()
    )
    return model.id if model else None


def configure_litellm(provider: LlmProvider) -> dict[str, str]:
    """Return per-request LiteLLM kwargs for a provider (no os.environ mutation)."""
    kwargs: dict[str, str] = {}
    mappings = _PREFIX_ENV_FIELDS.get(provider.litellm_prefix, ())
    mapped_env_keys = {m[1] for m in mappings}

    for field, env_key in mappings:
        value = getattr(provider, field, None) or (provider.extra_env or {}).get(env_key)
        if not value:
            continue
        if field == "api_key":
            kwargs["api_key"] = str(value)
        elif field == "api_base_url":
            kwargs["api_base"] = str(value).rstrip("/")

    for env_key, value in (provider.extra_env or {}).items():
        if value and env_key not in mapped_env_keys:
            # LiteLLM accepts many provider-specific params as call-time kwargs.
            kwargs[env_key.lower()] = str(value)

    return kwargs


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
        # Serializing admin default-reassignment against concurrent boots/toggles
        # keeps the single-default invariant inside the clear+set+commit below.
        _acquire_registry_lock(db)
        model.is_default = False
        replacement = (
            _enabled_chat_query(db)
            .filter(LlmModel.id != model_id, LlmModel.vision_only.is_(False))
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
    vision_only: bool = False,
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
        vision_only=vision_only,
        supports_streaming=supports_streaming,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        sort_order=sort_order,
        meta=meta or {},
    )
    db.add(model)
    return model


def _disable_provider_models(db: Session, provider: LlmProvider) -> bool:
    """Turn off a provider + its models (pool and admin toggles respect is_enabled)."""
    dirty = False
    if provider.is_enabled:
        provider.is_enabled = False
        dirty = True
    for model in db.query(LlmModel).filter(LlmModel.provider_id == provider.id).all():
        if model.is_enabled or model.is_default:
            model.is_enabled = False
            if model.is_default:
                model.is_default = False
            dirty = True
    return dirty


def _disable_stepfun(db: Session, stepfun: LlmProvider) -> bool:
    return _disable_provider_models(db, stepfun)


def _disable_zai(db: Session, zai: LlmProvider) -> bool:
    dirty = _disable_provider_models(db, zai)
    if (zai.api_key or "").strip():
        zai.api_key = None
        dirty = True
    return dirty


def _acquire_registry_lock(db: Session) -> None:
    """Block until this transaction holds the registry-mutation advisory lock.

    Transaction-scoped (pg_advisory_xact_lock), so it releases automatically on
    commit/rollback — no manual unlock. Serializes concurrent boots (rolling
    updates) so the clear-default → set-default → commit sequence runs in full
    before another pod can observe/flip the same rows. See ``_REGISTRY_LOCK_KEY``.
    """
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _REGISTRY_LOCK_KEY})


def _clear_defaults_for_kind(db: Session, kind: LlmModelKind) -> None:
    # synchronize_session="fetch" (NOT False): also clear is_default on rows already
    # loaded/dirtied in this session. With False, a model set is_default=True earlier in
    # the same transaction (e.g. the DeepSeek block) stays True in-session, so a later
    # default switch flushes TWO is_default=True rows and hits uq_llm_models_default_per_kind,
    # aborting the whole registry bootstrap (which then never sets vision_only/keys either).
    db.query(LlmModel).filter(LlmModel.kind == kind.value, LlmModel.is_default.is_(True)).update(
        {"is_default": False},
        synchronize_session="fetch",
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
        api_key=settings.zai_api_key or None if settings.zai_enabled else None,
    )
    if settings.zai_enabled:
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
        if glm and not glm.is_enabled and settings.zai_api_key:
            glm.is_enabled = True
            dirty = True
        if not zai.is_enabled:
            zai.is_enabled = True
            dirty = True
        if before == 0:
            dirty = True
    else:
        if _disable_zai(db, zai):
            dirty = True

    deepseek = _upsert_provider(
        db,
        slug="deepseek",
        display_name="DeepSeek",
        litellm_prefix="openai",
        api_base_url=settings.deepseek_api_base.rstrip("/") or None,
        api_key=settings.deepseek_api_key or None,
    )
    before_ds = (
        db.query(LlmModel)
        .filter(LlmModel.provider_id == deepseek.id, LlmModel.slug == "deepseek-v4-flash")
        .count()
    )
    _upsert_model(
        db,
        provider=deepseek,
        slug="deepseek-v4-flash",
        litellm_model="openai/deepseek-v4-flash",
        display_name="DeepSeek V4 Flash",
        kind=LlmModelKind.chat,
        sort_order=4,
        max_input_tokens=128_000,
        max_output_tokens=8_192,
        update_fields=True,
    )
    if before_ds == 0:
        dirty = True
    deepseek_model = (
        db.query(LlmModel)
        .filter(LlmModel.provider_id == deepseek.id, LlmModel.slug == "deepseek-v4-flash")
        .first()
    )
    # When a DeepSeek key is configured, pin deepseek-v4-flash as the default chat
    # model (and thus the pinned generation model). Runs every boot, so it tracks
    # key presence without a manual admin step — and takes priority over Step Fun.
    if settings.deepseek_api_key and deepseek_model and not deepseek_model.is_default:
        _clear_defaults_for_kind(db, LlmModelKind.chat)
        deepseek_model.is_default = True
        dirty = True

    stepfun = _upsert_provider(
        db,
        slug="stepfun",
        display_name="Step Fun",
        litellm_prefix="openai",
        api_base_url=settings.stepfun_api_base.rstrip("/") or None,
        api_key=settings.stepfun_api_key or None if settings.stepfun_enabled else None,
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
    # step-3.7-flash — newer Step Fun flash model; kept as the first failover after the
    # primary (step-3.5-flash). Comparable quality, typically a touch faster.
    before_37 = (
        db.query(LlmModel)
        .filter(LlmModel.provider_id == stepfun.id, LlmModel.slug == "step-3.7-flash")
        .count()
    )
    _upsert_model(
        db,
        provider=stepfun,
        slug="step-3.7-flash",
        litellm_model="openai/step-3.7-flash",
        display_name="Step 3.7 Flash",
        kind=LlmModelKind.chat,
        sort_order=6,
        max_input_tokens=128_000,
        max_output_tokens=8_192,
        update_fields=True,
    )
    if before_37 == 0:
        dirty = True
    stepfun_model = (
        db.query(LlmModel)
        .filter(LlmModel.provider_id == stepfun.id, LlmModel.slug == "step-3.5-flash")
        .first()
    )
    if stepfun_model:
        meta = dict(stepfun_model.meta or {})
        changed = False
        if meta.pop("thinking_disabled", None) is not None:
            changed = True
        # Stale low caps (e.g. 800) exhaust reasoning tokens before content.
        cap = meta.get("max_tokens")
        if isinstance(cap, int) and cap < MIN_MODEL_META_MAX_TOKENS:
            meta.pop("max_tokens", None)
            changed = True
        if changed:
            stepfun_model.meta = meta or None
            dirty = True
    if settings.stepfun_enabled:
        if stepfun_model and not stepfun_model.is_enabled:
            stepfun_model.is_enabled = True
            dirty = True
        if not stepfun.is_enabled:
            stepfun.is_enabled = True
            dirty = True
        step37 = (
            db.query(LlmModel)
            .filter(LlmModel.provider_id == stepfun.id, LlmModel.slug == "step-3.7-flash")
            .first()
        )
        if step37 and not step37.is_enabled:
            step37.is_enabled = True
            dirty = True
        # When a Step Fun key is configured (and DeepSeek isn't — DeepSeek is the
        # primary), pin step-3.5-flash as the default chat model on existing DBs too.
        if (
            settings.stepfun_api_key
            and not settings.deepseek_api_key
            and stepfun_model
            and not stepfun_model.is_default
        ):
            _clear_defaults_for_kind(db, LlmModelKind.chat)
            stepfun_model.is_default = True
            dirty = True
    else:
        if _disable_stepfun(db, stepfun):
            dirty = True
            if settings.deepseek_api_key and deepseek_model and not deepseek_model.is_default:
                _clear_defaults_for_kind(db, LlmModelKind.chat)
                deepseek_model.is_default = True
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
            supports_image_input=True,
            vision_only=True,
            update_fields=True,
        )
        if before == 0:
            dirty = True
        # MiMo v2.5 (Xiaomi) reads images — it's the VISION-ONLY model for Mains OCR
        # (kept out of the text pool so the metered plan is billed for vision alone).
        # update_fields never touches these bool flags on an existing row, so force them
        # here so live DBs light up on boot; self-healing re-disables it below if the
        # openai provider ends up without a key.
        mimo = (
            db.query(LlmModel)
            .filter(LlmModel.provider_id == openai.id, LlmModel.slug == "mimo-v2.5")
            .first()
        )
        if mimo and (not mimo.supports_image_input or not mimo.is_enabled or not mimo.vision_only):
            mimo.supports_image_input = True
            mimo.is_enabled = True
            mimo.vision_only = True
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

    # Serialize against concurrent boots: the default-flip below (clear + set +
    # commit) must be atomic relative to another pod doing the same, or both
    # pods flush two is_default=True rows and trip uq_llm_models_default_per_kind.
    _acquire_registry_lock(db)

    if ensure_registry_providers(db, settings):
        dirty = True

    if settings.deepseek_api_key or settings.deepseek_api_base:
        provider = db.query(LlmProvider).filter(LlmProvider.slug == "deepseek").first()
        if provider:
            if settings.deepseek_api_key and (force_keys or not provider.api_key):
                provider.api_key = settings.deepseek_api_key
                dirty = True
            if settings.deepseek_api_base and (force_keys or not provider.api_base_url):
                provider.api_base_url = settings.deepseek_api_base.rstrip("/")
                dirty = True

    if settings.zai_enabled and (settings.zai_api_key or settings.zai_api_base):
        provider = db.query(LlmProvider).filter(LlmProvider.slug == "zai").first()
        if provider:
            if settings.zai_api_key and (force_keys or not provider.api_key):
                provider.api_key = settings.zai_api_key
                dirty = True
            if settings.zai_api_base and (force_keys or not provider.api_base_url):
                provider.api_base_url = settings.zai_api_base.rstrip("/")
                dirty = True

    if settings.stepfun_enabled and (settings.stepfun_api_key or settings.stepfun_api_base):
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
        if not provider_has_api_key(model.provider) and model.provider.slug != "local":
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

    # Match by the full litellm_model string across ALL providers. The litellm prefix
    # cannot identify the provider here: zai, stepfun and openrouter all use the
    # "openai" prefix, so resolving by prefix could never select e.g. openai/glm-4.7
    # (a zai model) and the env default silently fell back to another provider's model.
    model = (
        db.query(LlmModel)
        .filter(
            LlmModel.kind == LlmModelKind.chat,
            LlmModel.litellm_model == litellm_model,
        )
        .first()
    )
    if not model:
        slug = litellm_model.split("/", 1)[-1].replace("/", "-")
        model = (
            db.query(LlmModel)
            .filter(LlmModel.kind == LlmModelKind.chat, LlmModel.slug == slug)
            .first()
        )
    # A vision-only model must never become the TEXT default, even if LITELLM_MODEL
    # points at it — it's for require_vision calls only (OCR/images).
    if not model or not model.is_enabled or model.vision_only:
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
        # Empty DB: seed everything in one transaction. Two pods can both observe
        # an empty DB during a fresh deploy and both seed defaults — serialize so
        # the partial-unique constraint on is_default can't be tripped.
        _acquire_registry_lock(db)
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
            # MiMo v2.5 reads images — the VISION-ONLY model for Mains OCR (never serves
            # text). Enabled when a key is configured; require_vision routing selects it.
            is_enabled=bool(settings.openai_api_key),
            supports_image_input=True,
            vision_only=True,
            sort_order=10,
            max_input_tokens=128_000,
            max_output_tokens=8_192,
        )

        if settings.zai_enabled:
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

        deepseek = _upsert_provider(
            db,
            slug="deepseek",
            display_name="DeepSeek",
            litellm_prefix="openai",
            api_base_url=settings.deepseek_api_base.rstrip("/") or None,
            api_key=settings.deepseek_api_key or None,
        )
        # deepseek-v4-flash is the pinned default generation model when a key is
        # configured — fast, cheap, automatic server-side prefix caching.
        _upsert_model(
            db,
            provider=deepseek,
            slug="deepseek-v4-flash",
            litellm_model="openai/deepseek-v4-flash",
            display_name="DeepSeek V4 Flash",
            kind=LlmModelKind.chat,
            is_default=bool(settings.deepseek_api_key),
            sort_order=4,
            max_input_tokens=128_000,
            max_output_tokens=8_192,
        )

        if settings.stepfun_enabled:
            stepfun = _upsert_provider(
                db,
                slug="stepfun",
                display_name="Step Fun",
                litellm_prefix="openai",
                api_base_url=settings.stepfun_api_base.rstrip("/") or None,
                api_key=settings.stepfun_api_key or None,
            )
            stepfun_is_default = bool(settings.stepfun_api_key) and not settings.deepseek_api_key
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
            _upsert_model(
                db,
                provider=stepfun,
                slug="step-3.7-flash",
                litellm_model="openai/step-3.7-flash",
                display_name="Step 3.7 Flash",
                kind=LlmModelKind.chat,
                is_default=False,
                sort_order=6,
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
