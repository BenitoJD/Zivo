"""Idempotent local dev database seeding (development DB only)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Account as User
from app.models.llm import LlmModel, LlmProvider
from app.services.auth import hash_password
from app.services.llm_registry import resolve_chat_model, seed_llm_registry_from_env


@dataclass(frozen=True)
class SeedUserSpec:
    username: str
    password: str
    is_admin: bool


@dataclass
class SeedReport:
    users: list[tuple[SeedUserSpec, bool]] = field(default_factory=list)
    llm_provider_count: int = 0
    llm_model_count: int = 0
    default_chat_model: str | None = None
    providers_with_keys: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def default_seed_users() -> tuple[SeedUserSpec, ...]:
    return (
        SeedUserSpec("dev", os.environ.get("SEED_DEV_PASSWORD", "devpass1"), False),
        SeedUserSpec("admin", os.environ.get("SEED_ADMIN_PASSWORD", "adminpass1"), True),
    )


def assert_local_dev_seed_allowed() -> None:
    settings = get_settings()
    if settings.environment != "development":
        raise RuntimeError(
            f"Refusing to seed users when ENVIRONMENT={settings.environment!r} (development only)"
        )
    parsed = urlparse(settings.database_url.replace("postgresql+psycopg", "postgresql"))
    host = parsed.hostname or "localhost"
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError(f"Refusing to seed users against non-local database host: {host}")


def upsert_seed_user(db: Session, spec: SeedUserSpec) -> tuple[User, bool]:
    """Insert or update a seed user. Returns ``(user, created)``."""
    user = db.query(User).filter(User.username == spec.username).first()
    password_hash = hash_password(spec.password)
    if user:
        user.password_hash = password_hash
        user.is_admin = spec.is_admin
        return user, False
    user = User(username=spec.username, password_hash=password_hash, is_admin=spec.is_admin)
    db.add(user)
    return user, True


def seed_local_users(
    db: Session,
    users: tuple[SeedUserSpec, ...] | None = None,
) -> list[tuple[SeedUserSpec, bool]]:
    """Seed local dev users. Returns ``(spec, created)`` for each account."""
    assert_local_dev_seed_allowed()
    specs = users if users is not None else default_seed_users()
    results: list[tuple[SeedUserSpec, bool]] = []
    for spec in specs:
        _, created = upsert_seed_user(db, spec)
        results.append((spec, created))
    db.commit()
    return results


def seed_local_database(
    db: Session,
    *,
    users: bool = True,
    llm: bool = True,
    demo_embeddings: bool = False,
) -> SeedReport:
    """Seed all local dev data: users, LLM registry keys/defaults, optional demo embeddings."""
    assert_local_dev_seed_allowed()
    report = SeedReport()

    if users:
        report.users = seed_local_users(db)

    if llm:
        seed_llm_registry_from_env(db, force_keys=True)
        report.llm_provider_count = db.query(LlmProvider).count()
        report.llm_model_count = db.query(LlmModel).count()
        report.providers_with_keys = [
            p.slug for p in db.query(LlmProvider).all() if p.api_key or p.slug == "local"
        ]
        try:
            default = resolve_chat_model(db)
            report.default_chat_model = default.record.litellm_model
            if default.provider.slug != "local" and not default.provider.api_key:
                report.warnings.append(
                    f"Default chat model {default.record.litellm_model!r} has no API key — "
                    f"set the provider key in backend/.env.local"
                )
        except Exception as exc:
            report.warnings.append(f"Chat model not ready: {exc}")

        settings = get_settings()
        if not any(
            [
                settings.openai_api_key,
                settings.zai_api_key,
                settings.openrouter_api_key,
                settings.gemini_api_key,
                settings.anthropic_api_key,
            ]
        ):
            report.warnings.append(
                "No LLM API keys in env — add OPENAI_API_KEY, ZAI_API_KEY, OPENROUTER_API_KEY, "
                "GEMINI_API_KEY, or ANTHROPIC_API_KEY to backend/.env.local"
            )

    if demo_embeddings:
        try:
            from app.services.demo_seed import embed_demo_chunks_if_needed, ensure_demo_document

            ensure_demo_document(db)
            embed_demo_chunks_if_needed()
        except Exception as exc:
            report.warnings.append(f"Demo embeddings skipped: {exc}")
    else:
        try:
            from app.services.demo_seed import ensure_demo_document

            ensure_demo_document(db)
        except Exception as exc:
            report.warnings.append(f"Demo document skipped: {exc}")

    return report
