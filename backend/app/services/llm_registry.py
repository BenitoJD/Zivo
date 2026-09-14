"""LLM provider + model registry — resolve models and configure LiteLLM."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session, joinedload

from app.config import Settings, get_settings
from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.models.llm import LlmModel, LlmModelKind, LlmProvider
from app.services.llm_route import evaluate_llm_route
from app.services.presence import evaluate_presence
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

_FIELD_SETTERS = {
    "api_key": lambda kwargs, value: kwargs.__setitem__("api_key", str(value)),
    "api_base_url": lambda kwargs, value: kwargs.__setitem__("api_base", str(value).rstrip("/")),
}


def _raise(exc: BaseException) -> None:
    raise exc


def _http(status: int, detail: str) -> None:
    raise HTTPException(status_code=status, detail=detail)


def _mark(obj: object, **fields: object) -> bool:
    for key, value in fields.items():
        setattr(obj, key, value)
    return True


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


def _resolve_by_id(db: Session, model_id: uuid.UUID, require_vision: bool) -> ResolvedLlmModel:
    model = _enabled_chat_query(db).filter(LlmModel.id == model_id).first()

    def _check() -> ResolvedLlmModel:
        pick(
            require_vision and not model.supports_image_input,
            lambda: _http(400, "Selected model does not support images"),
            lambda: None,
        )
        return ResolvedLlmModel(record=model, provider=model.provider)

    return apply(
        evaluate_presence(model).action,
        {
            "missing": lambda: _http(404, "Model not found"),
            "empty": lambda: _http(404, "Model not found"),
            "ok": _check,
        },
    )


def _resolve_default(db: Session, require_vision: bool) -> ResolvedLlmModel:
    query = pick(
        require_vision,
        lambda: _enabled_chat_query(db).filter(LlmModel.supports_image_input.is_(True)),
        lambda: _enabled_chat_query(db).filter(LlmModel.vision_only.is_(False)),
    )
    default = query.filter(LlmModel.is_default.is_(True)).order_by(LlmModel.sort_order).first()

    def _fallback() -> ResolvedLlmModel:
        fallback = query.order_by(LlmModel.sort_order, LlmModel.display_name).first()
        return apply(
            evaluate_llm_route(has_primary=False, has_fallback=bool(fallback)).action,
            {
                "use_fallback": lambda: ResolvedLlmModel(record=fallback, provider=fallback.provider),
                "use_primary": lambda: _http(503, "No chat model configured"),
                "skip": lambda: _http(503, "No chat model configured"),
            },
        )

    return apply(
        evaluate_llm_route(has_primary=bool(default), has_fallback=True).action,
        {
            "use_primary": lambda: ResolvedLlmModel(record=default, provider=default.provider),
            "use_fallback": _fallback,
            "skip": lambda: _http(503, "No chat model configured"),
        },
    )


def resolve_chat_model(
    db: Session,
    *,
    model_id: uuid.UUID | None = None,
    require_vision: bool = False,
) -> ResolvedLlmModel:
    return apply(
        evaluate_presence(model_id).action,
        {
            "ok": lambda: _resolve_by_id(db, model_id, require_vision),
            "missing": lambda: _resolve_default(db, require_vision),
            "empty": lambda: _resolve_default(db, require_vision),
        },
    )


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
    return apply(
        evaluate_presence(model).action,
        {
            "missing": lambda: _http(503, "No embedding model configured"),
            "empty": lambda: _http(503, "No embedding model configured"),
            "ok": lambda: ResolvedLlmModel(record=model, provider=model.provider),
        },
    )


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
        .filter(LlmModel.vision_only.is_(False))
        .order_by(LlmModel.is_default.desc(), LlmModel.sort_order, LlmModel.display_name)
        .first()
    )
    return getattr(model, "id", None)


def _named_enabled_chat_id(db: Session, name: str, *filters: object) -> uuid.UUID | None:
    model = (
        _enabled_chat_query(db)
        .filter(func.lower(LlmModel.display_name) == name.lower(), *filters)
        .order_by(LlmModel.sort_order)
        .first()
    )
    return getattr(model, "id", None)


def draft_chat_model_id(db: Session) -> uuid.UUID | None:
    """The model to use for writing DRAFTS, if a separate draft model is configured.

    Lets ops point drafting at a faster/cheaper model (latency lever) while the
    critic + answer-key verifier keep using the strong default. Returns None when
    `draft_model_name` is unset or doesn't match an enabled chat model, so callers
    fall back to `default_chat_model_id`.
    """
    name = (get_settings().draft_model_name or "").strip()
    return apply(
        evaluate_presence(name).action,
        {
            "missing": lambda: None,
            "empty": lambda: None,
            "ok": lambda: _named_enabled_chat_id(db, name, LlmModel.vision_only.is_(False)),
        },
    )


def vision_chat_model_id(db: Session) -> uuid.UUID | None:
    """The vision model to use for reading answer images (Mains OCR), if pinned.

    Plug-and-play: set `vision_model_name` (env/settings) to any enabled
    vision-capable model's display name to pin OCR to it. Returns None when unset
    or unmatched, so callers fall back to `require_vision=True` auto-routing (the
    enabled default vision model). Mirrors `draft_model_name`.
    """
    name = (get_settings().vision_model_name or "").strip()
    return apply(
        evaluate_presence(name).action,
        {
            "missing": lambda: None,
            "empty": lambda: None,
            "ok": lambda: _named_enabled_chat_id(
                db, name, LlmModel.supports_image_input.is_(True)
            ),
        },
    )


def configure_litellm(provider: LlmProvider) -> dict[str, str]:
    """Return per-request LiteLLM kwargs for a provider (no os.environ mutation)."""
    kwargs: dict[str, str] = {}
    mappings = _PREFIX_ENV_FIELDS.get(provider.litellm_prefix, ())
    mapped_env_keys = {m[1] for m in mappings}

    for field, env_key in mappings:
        value = getattr(provider, field, None) or (provider.extra_env or {}).get(env_key)
        pick(
            not value,
            lambda: None,
            lambda f=field, v=value: _FIELD_SETTERS.get(f, lambda *_a: None)(kwargs, v),
        )

    for env_key, value in (provider.extra_env or {}).items():
        pick(
            bool(value) and env_key not in mapped_env_keys,
            lambda k=env_key, v=value: kwargs.__setitem__(k.lower(), str(v)),
            lambda: None,
        )

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
    return provider.slug == "local" or bool((provider.api_key or "").strip())


def set_chat_model_enabled(db: Session, model_id: uuid.UUID, *, enabled: bool) -> LlmModel:
    model = (
        db.query(LlmModel)
        .options(joinedload(LlmModel.provider))
        .filter(LlmModel.id == model_id, LlmModel.kind == LlmModelKind.chat)
        .first()
    )
    apply(
        evaluate_presence(model).action,
        {
            "missing": lambda: _http(404, "Model not found"),
            "empty": lambda: _http(404, "Model not found"),
            "ok": lambda: None,
        },
    )

    model.is_enabled = enabled

    def _reassign() -> None:
        _acquire_registry_lock(db)
        model.is_default = False
        replacement = (
            _enabled_chat_query(db)
            .filter(LlmModel.id != model_id, LlmModel.vision_only.is_(False))
            .order_by(LlmModel.sort_order, LlmModel.display_name)
            .first()
        )
        pick(
            bool(replacement),
            lambda: (_clear_defaults_for_kind(db, LlmModelKind.chat), setattr(replacement, "is_default", True)),
            lambda: None,
        )

    pick(not enabled and model.is_default, _reassign, lambda: None)
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
    default_chat_id = getattr(
        next(filter(lambda m: m.kind == LlmModelKind.chat and m.is_default, models), None),
        "id",
        None,
    )
    default_embed_id = getattr(
        next(filter(lambda m: m.kind == LlmModelKind.embedding and m.is_default, models), None),
        "id",
        None,
    )
    return models, default_chat_id, default_embed_id


# GLM 4.x models default to a reasoning/"thinking" mode that emits a 1-3k-token
# trace we discard (only the small MCQ JSON block is parsed). Disabling it is a
# ~5x latency / ~14x token reduction for generation. See llm_pool.litellm_provider_kwargs.
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
    max_concurrency: int | None = None,
) -> LlmProvider:
    provider = db.query(LlmProvider).filter(LlmProvider.slug == slug).first()

    def _update() -> LlmProvider:
        pick(bool(api_base_url), lambda: setattr(provider, "api_base_url", api_base_url), lambda: None)
        pick(bool(api_key), lambda: setattr(provider, "api_key", api_key), lambda: None)
        pick(
            max_concurrency is not None and provider.max_concurrency is None,
            lambda: setattr(provider, "max_concurrency", max_concurrency),
            lambda: None,
        )
        return provider

    def _create() -> LlmProvider:
        created = LlmProvider(
            slug=slug,
            display_name=display_name,
            litellm_prefix=litellm_prefix,
            api_base_url=api_base_url,
            api_key=api_key,
            max_concurrency=max_concurrency,
        )
        db.add(created)
        db.flush()
        return created

    return apply(
        evaluate_presence(provider).action,
        {"ok": _update, "missing": _create, "empty": _create},
    )


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

    def _update() -> LlmModel:
        def _fields() -> None:
            model.litellm_model = litellm_model
            model.display_name = display_name
            model.sort_order = sort_order
            pick(
                max_input_tokens is not None,
                lambda: setattr(model, "max_input_tokens", max_input_tokens),
                lambda: None,
            )
            pick(
                max_output_tokens is not None,
                lambda: setattr(model, "max_output_tokens", max_output_tokens),
                lambda: None,
            )
            pick(meta is not None, lambda: setattr(model, "meta", meta), lambda: None)

        pick(update_fields, _fields, lambda: None)
        pick(is_default, lambda: setattr(model, "is_default", True), lambda: None)
        return model

    def _create() -> LlmModel:
        created = LlmModel(
            provider_id=provider.id,
            slug=slug,
            litellm_model=litellm_model,
            display_name=display_name,
            kind=kind.value,
            is_enabled=choose(is_enabled is not None, is_enabled, True),
            is_default=is_default,
            supports_image_input=supports_image_input,
            vision_only=vision_only,
            supports_streaming=supports_streaming,
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
            sort_order=sort_order,
            meta=meta or {},
        )
        db.add(created)
        return created

    return apply(
        evaluate_presence(model).action,
        {"ok": _update, "missing": _create, "empty": _create},
    )


def _disable_provider_models(db: Session, provider: LlmProvider) -> bool:
    """Turn off a provider + its models (pool and admin toggles respect is_enabled)."""
    dirty = [False]
    pick(
        provider.is_enabled,
        lambda: dirty.__setitem__(0, _mark(provider, is_enabled=False)),
        lambda: None,
    )

    def _disable_model(model: LlmModel) -> None:
        def _do() -> None:
            model.is_enabled = False
            pick(model.is_default, lambda: setattr(model, "is_default", False), lambda: None)
            dirty[0] = True

        pick(model.is_enabled or model.is_default, _do, lambda: None)

    for model in db.query(LlmModel).filter(LlmModel.provider_id == provider.id).all():
        _disable_model(model)
    return dirty[0]


def _disable_stepfun(db: Session, stepfun: LlmProvider) -> bool:
    return _disable_provider_models(db, stepfun)


def _disable_zai(db: Session, zai: LlmProvider) -> bool:
    dirty = _disable_provider_models(db, zai)

    def _clear_key() -> bool:
        zai.api_key = None
        return True

    cleared = pick(bool((zai.api_key or "").strip()), _clear_key, lambda: False)
    return dirty or cleared


def _acquire_registry_lock(db: Session) -> None:
    """Block until this transaction holds the registry-mutation advisory lock.

    Transaction-scoped (pg_advisory_xact_lock), so it releases automatically on
    commit/rollback. Serializes concurrent boots (rolling updates) so the
    clear-default → set-default → commit sequence runs in full before another
    pod can observe/flip the same rows. See ``_REGISTRY_LOCK_KEY``.
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


def _pin_default(db: Session, model: LlmModel) -> bool:
    def _pin() -> bool:
        _clear_defaults_for_kind(db, LlmModelKind.chat)
        model.is_default = True
        return True

    return pick(bool(model) and not model.is_default, _pin, lambda: False)


def _ensure_zai(db: Session, settings: Settings) -> bool:
    zai = _upsert_provider(
        db,
        slug="zai",
        display_name="Z.AI",
        litellm_prefix="openai",
        api_base_url=settings.zai_api_base.rstrip("/") or None,
        api_key=choose(settings.zai_enabled, settings.zai_api_key or None, None),
    )

    def _enable() -> bool:
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
        glm = (
            db.query(LlmModel)
            .filter(LlmModel.provider_id == zai.id, LlmModel.slug == "glm-4.7")
            .first()
        )
        return any(
            [
                before == 0,
                pick(
                    bool(glm) and not (glm.meta or {}).get("thinking_disabled"),
                    lambda: _mark(glm, meta=GLM_THINKING_DISABLED_META),
                    lambda: False,
                ),
                pick(
                    bool(glm) and not glm.is_enabled and bool(settings.zai_api_key),
                    lambda: _mark(glm, is_enabled=True),
                    lambda: False,
                ),
                pick(not zai.is_enabled, lambda: _mark(zai, is_enabled=True), lambda: False),
            ]
        )

    return pick(settings.zai_enabled, _enable, lambda: _disable_zai(db, zai))


def _ensure_deepseek(db: Session, settings: Settings) -> tuple[bool, LlmModel | None]:
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
    deepseek_model = (
        db.query(LlmModel)
        .filter(LlmModel.provider_id == deepseek.id, LlmModel.slug == "deepseek-v4-flash")
        .first()
    )
    # Auto-pin deepseek only when it is the configured default. Unconditionally
    # pinning on key presence raced sync_default_chat_model_from_env: with both a
    # deepseek key and LITELLM_MODEL naming another provider, the refresh flushed
    # two is_default=True chat rows and tripped uq_llm_models_default_per_kind,
    # aborting the whole bootstrap (tutor then 500s "could not reach tutor").
    litellm_slug = settings.litellm_model.strip().split("/", 1)[-1].replace("/", "-")
    dirty = any(
        [
            before_ds == 0,
            pick(
                bool(settings.deepseek_api_key)
                and bool(deepseek_model)
                and not deepseek_model.is_default
                and pick(
                    bool(settings.litellm_model.strip()),
                    lambda: litellm_slug == deepseek_model.slug,
                    lambda: True,
                ),
                lambda: _pin_default(db, deepseek_model),
                lambda: False,
            ),
        ]
    )
    return dirty, deepseek_model


def _clean_stepfun_meta(stepfun_model: LlmModel | None) -> bool:
    def _clean() -> bool:
        meta = dict(stepfun_model.meta or {})
        changed = meta.pop("thinking_disabled", None) is not None
        cap = meta.get("max_tokens")

        def _pop_cap() -> bool:
            meta.pop("max_tokens", None)
            return True

        changed = pick(
            isinstance(cap, int) and cap < MIN_MODEL_META_MAX_TOKENS,
            _pop_cap,
            lambda: changed,
        ) or changed
        pick(changed, lambda: setattr(stepfun_model, "meta", meta or None), lambda: None)
        return changed

    return pick(bool(stepfun_model), _clean, lambda: False)


def _ensure_stepfun(db: Session, settings: Settings, deepseek_model: LlmModel | None) -> bool:
    stepfun = _upsert_provider(
        db,
        slug="stepfun",
        display_name="Step Fun",
        litellm_prefix="openai",
        api_base_url=settings.stepfun_api_base.rstrip("/") or None,
        api_key=choose(settings.stepfun_enabled, settings.stepfun_api_key or None, None),
        max_concurrency=8,
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
    stepfun_model = (
        db.query(LlmModel)
        .filter(LlmModel.provider_id == stepfun.id, LlmModel.slug == "step-3.5-flash")
        .first()
    )
    dirty = (before == 0) or (before_37 == 0) or _clean_stepfun_meta(stepfun_model)

    def _enable() -> bool:
        step37 = (
            db.query(LlmModel)
            .filter(LlmModel.provider_id == stepfun.id, LlmModel.slug == "step-3.7-flash")
            .first()
        )
        return any(
            [
                pick(
                    bool(stepfun_model) and not stepfun_model.is_enabled,
                    lambda: _mark(stepfun_model, is_enabled=True),
                    lambda: False,
                ),
                pick(not stepfun.is_enabled, lambda: _mark(stepfun, is_enabled=True), lambda: False),
                pick(
                    bool(step37) and not step37.is_enabled,
                    lambda: _mark(step37, is_enabled=True),
                    lambda: False,
                ),
                pick(
                    bool(settings.stepfun_api_key)
                    and not settings.deepseek_api_key
                    and bool(stepfun_model)
                    and not stepfun_model.is_default,
                    lambda: _pin_default(db, stepfun_model),
                    lambda: False,
                ),
            ]
        )

    def _disable() -> bool:
        extra = _disable_stepfun(db, stepfun)
        pinned = pick(
            extra
            and bool(settings.deepseek_api_key)
            and bool(deepseek_model)
            and not deepseek_model.is_default,
            lambda: _pin_default(db, deepseek_model),
            lambda: False,
        )
        return extra or pinned

    return any([dirty, pick(settings.stepfun_enabled, _enable, _disable)])


def _ensure_openrouter(db: Session, settings: Settings) -> bool:
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
    return before == 0


def _ensure_openai_mimo(db: Session, settings: Settings) -> bool:
    openai = db.query(LlmProvider).filter(LlmProvider.slug == "openai").first()
    created = False

    def _create() -> LlmProvider:
        nonlocal created
        created = True
        return _upsert_provider(
            db,
            slug="openai",
            display_name="OpenAI-compatible",
            litellm_prefix="openai",
            api_base_url=settings.openai_api_base or None,
            api_key=settings.openai_api_key or None,
        )

    openai = pick(
        openai is None and bool(settings.openai_api_key or settings.openai_api_base),
        _create,
        lambda: openai,
    )

    def _mimo() -> bool:
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
        mimo = (
            db.query(LlmModel)
            .filter(LlmModel.provider_id == openai.id, LlmModel.slug == "mimo-v2.5")
            .first()
        )
        return any(
            [
                before == 0,
                pick(
                    bool(mimo)
                    and (not mimo.supports_image_input or not mimo.is_enabled or not mimo.vision_only),
                    lambda: _mark(mimo, supports_image_input=True, is_enabled=True, vision_only=True),
                    lambda: False,
                ),
            ]
        )

    mimo_dirty = pick(bool(openai), _mimo, lambda: False)
    return created or mimo_dirty


def ensure_registry_providers(db: Session, settings: Settings | None = None) -> bool:
    """Upsert known providers/models so existing DBs pick up new routes (e.g. Z.AI GLM)."""
    settings = settings or get_settings()
    zai_dirty = _ensure_zai(db, settings)
    ds_dirty, deepseek_model = _ensure_deepseek(db, settings)
    return any(
        [
            zai_dirty,
            ds_dirty,
            _ensure_stepfun(db, settings, deepseek_model),
            _ensure_openrouter(db, settings),
            _ensure_openai_mimo(db, settings),
        ]
    )


def _maybe_set_attr(provider: LlmProvider, attr: str, value: object, force_keys: bool) -> bool:
    def _set() -> bool:
        setattr(provider, attr, value)
        return True

    return pick(bool(value) and (force_keys or not getattr(provider, attr)), _set, lambda: False)


def _sync_provider_env(
    db: Session,
    slug: str,
    *,
    api_key: str,
    api_base: str,
    force_keys: bool,
    enabled: bool = True,
) -> bool:
    def _go() -> bool:
        provider = db.query(LlmProvider).filter(LlmProvider.slug == slug).first()

        def _apply() -> bool:
            d1 = _maybe_set_attr(provider, "api_key", api_key, force_keys)
            d2 = pick(
                bool(api_base) and (force_keys or not provider.api_base_url),
                lambda: _mark(provider, api_base_url=api_base.rstrip("/")),
                lambda: False,
            )
            return d1 or d2

        return pick(bool(provider), _apply, lambda: False)

    return pick(enabled and bool(api_key or api_base), _go, lambda: False)


def _heal_keyless(model: LlmModel) -> bool:
    def _do() -> bool:
        model.is_enabled = False
        pick(model.is_default, lambda: setattr(model, "is_default", False), lambda: None)
        return True

    return apply(
        first_match(
            (
                Rule(when=(Pred("local", "truthy"),), action="keep"),
                Rule(when=(Pred("has_key", "truthy"),), action="keep"),
                Rule(when=(), action="disable"),
            ),
            {
                "local": model.provider.slug == "local",
                "has_key": provider_has_api_key(model.provider),
            },
        ).action,
        {"keep": lambda: False, "disable": _do},
    )


def refresh_llm_registry_from_env(
    db: Session,
    settings: Settings | None = None,
    *,
    force_keys: bool = False,
) -> bool:
    """Apply env API keys/base URLs and default chat model. Returns True if DB changed."""
    settings = settings or get_settings()
    _acquire_registry_lock(db)
    dirty = any(
        [
            ensure_registry_providers(db, settings),
            _sync_provider_env(
                db,
                "deepseek",
                api_key=settings.deepseek_api_key,
                api_base=settings.deepseek_api_base,
                force_keys=force_keys,
            ),
            _sync_provider_env(
                db,
                "zai",
                api_key=settings.zai_api_key,
                api_base=settings.zai_api_base,
                force_keys=force_keys,
                enabled=settings.zai_enabled,
            ),
            _sync_provider_env(
                db,
                "stepfun",
                api_key=settings.stepfun_api_key,
                api_base=settings.stepfun_api_base,
                force_keys=force_keys,
                enabled=settings.stepfun_enabled,
            ),
            _sync_provider_env(
                db,
                "openrouter",
                api_key=settings.openrouter_api_key,
                api_base=settings.openrouter_api_base,
                force_keys=force_keys,
            ),
            _sync_provider_env(
                db,
                "openai",
                api_key=settings.openai_api_key,
                api_base=settings.openai_api_base,
                force_keys=force_keys,
            ),
            _sync_provider_env(
                db, "gemini", api_key=settings.gemini_api_key, api_base="", force_keys=force_keys
            ),
            _sync_provider_env(
                db, "anthropic", api_key=settings.anthropic_api_key, api_base="", force_keys=force_keys
            ),
        ]
    )
    healed = list(
        map(
            _heal_keyless,
            db.query(LlmModel)
            .join(LlmProvider)
            .filter(LlmModel.kind == LlmModelKind.chat, LlmModel.is_enabled.is_(True))
            .all(),
        )
    )
    dirty = any([dirty, any(healed), sync_default_chat_model_from_env(db, settings), sync_default_embedding_model_from_env(db, settings)])
    pick(dirty, db.commit, lambda: None)
    return dirty


def sync_default_chat_model_from_env(db: Session, settings: Settings | None = None) -> bool:
    """Point the default chat model at ``settings.litellm_model`` when that row exists."""
    settings = settings or get_settings()
    litellm_model = settings.litellm_model.strip()

    def _sync() -> bool:
        model = (
            db.query(LlmModel)
            .filter(LlmModel.kind == LlmModelKind.chat, LlmModel.litellm_model == litellm_model)
            .first()
        )

        def _by_slug() -> LlmModel | None:
            slug = litellm_model.split("/", 1)[-1].replace("/", "-")
            return (
                db.query(LlmModel)
                .filter(LlmModel.kind == LlmModelKind.chat, LlmModel.slug == slug)
                .first()
            )

        model = pick(bool(model), lambda: model, _by_slug)

        def _maybe_pin() -> bool:
            current_default = (
                db.query(LlmModel)
                .filter(LlmModel.kind == LlmModelKind.chat, LlmModel.is_default.is_(True))
                .first()
            )
            return pick(
                bool(current_default) and current_default.id == model.id,
                lambda: False,
                lambda: (_clear_defaults_for_kind(db, LlmModelKind.chat), setattr(model, "is_default", True), True)[-1],
            )

        return pick(
            not model or not model.is_enabled or model.vision_only,
            lambda: False,
            _maybe_pin,
        )

    return pick(not litellm_model or "/" not in litellm_model, lambda: False, _sync)


def sync_default_embedding_model_from_env(db: Session, settings: Settings | None = None) -> bool:
    """Keep the default embedding row aligned with ``settings.embed_model``."""
    settings = settings or get_settings()
    embed_model = settings.embed_model.strip()

    def _sync() -> bool:
        model = (
            db.query(LlmModel)
            .filter(LlmModel.kind == LlmModelKind.embedding, LlmModel.is_default.is_(True))
            .first()
        )

        def _align() -> bool:
            display_name = "BGE small en v1.5 (embeddings)"
            meta = dict(model.meta or {})
            return any(
                [
                    pick(
                        model.litellm_model != embed_model,
                        lambda: _mark(model, litellm_model=embed_model),
                        lambda: False,
                    ),
                    pick(
                        model.display_name != display_name,
                        lambda: _mark(model, display_name=display_name),
                        lambda: False,
                    ),
                    pick(
                        meta.get("embed_dimension") != settings.embed_dimension,
                        lambda: (
                            meta.__setitem__("embed_dimension", settings.embed_dimension),
                            _mark(model, meta=meta),
                        )[-1],
                        lambda: False,
                    ),
                ]
            )

        return apply(
            evaluate_presence(model).action,
            {"missing": lambda: False, "empty": lambda: False, "ok": _align},
        )

    return pick(not embed_model, lambda: False, _sync)


def _seed_empty_registry(db: Session, settings: Settings) -> None:
    _acquire_registry_lock(db)
    local = _upsert_provider(db, slug="local", display_name="Local", litellm_prefix="local")
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
        is_enabled=bool(settings.openai_api_key),
        supports_image_input=True,
        vision_only=True,
        sort_order=10,
        max_input_tokens=128_000,
        max_output_tokens=8_192,
    )

    def _seed_zai() -> None:
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

    pick(settings.zai_enabled, _seed_zai, lambda: None)

    deepseek = _upsert_provider(
        db,
        slug="deepseek",
        display_name="DeepSeek",
        litellm_prefix="openai",
        api_base_url=settings.deepseek_api_base.rstrip("/") or None,
        api_key=settings.deepseek_api_key or None,
    )
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

    def _seed_stepfun() -> None:
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

    pick(settings.stepfun_enabled, _seed_stepfun, lambda: None)

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

    def _custom_default() -> None:
        _clear_defaults_for_kind(db, LlmModelKind.chat)
        target_slug = _provider_slug_for_litellm_model(settings.litellm_model)
        model_name = settings.litellm_model.split("/", 1)[-1]
        provider = db.query(LlmProvider).filter(LlmProvider.slug == target_slug).first()

        def _make() -> None:
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

        pick(bool(provider), _make, lambda: None)

    pick(
        not db.query(LlmModel).filter(LlmModel.kind == LlmModelKind.chat, LlmModel.is_default.is_(True)).count(),
        _custom_default,
        lambda: None,
    )
    db.commit()


def bootstrap_llm_registry_from_env(db: Session, settings: Settings | None = None) -> None:
    """Seed or refresh providers/models from env on startup (idempotent)."""
    settings = settings or get_settings()
    # Hold the registry lock BEFORE the empty-check: during a rolling deploy two
    # pods can both observe count() == 0, and the loser's seed then collides on
    # uq_llm_models_default_per_kind. The lock serializes the decision with the
    # other pod's commit (pg_advisory_xact_lock re-enters safely in _seed/_refresh).
    _acquire_registry_lock(db)
    pick(
        db.query(LlmProvider).count() == 0,
        lambda: _seed_empty_registry(db, settings),
        lambda: refresh_llm_registry_from_env(db, settings, force_keys=True),
    )


def seed_llm_registry_from_env(db: Session, settings: Settings | None = None, *, force_keys: bool = True) -> None:
    """Dev seed: ensure registry rows exist and env keys/default model are applied."""
    settings = settings or get_settings()
    pick(
        db.query(LlmProvider).count() == 0,
        lambda: bootstrap_llm_registry_from_env(db, settings),
        lambda: refresh_llm_registry_from_env(db, settings, force_keys=force_keys),
    )
