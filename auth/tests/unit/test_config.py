"""Configuration validation for the auth service."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def _make_settings(**overrides: object) -> Settings:
    base = {
        "secret_key": "dev-secret-change-me",
        "csrf_secret": "dev-csrf-change-me",
        "environment": "production",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class TestSecretKeyValidation:
    def test_rejects_dev_secret_key_in_production(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _make_settings()
        assert "SECRET_KEY" in str(exc.value)

    def test_rejects_dev_csrf_secret_in_production(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _make_settings(secret_key="real-prod-secret-32chars-or-more")
        assert "CSRF_SECRET" in str(exc.value)

    def test_accepts_unique_secrets_in_production(self) -> None:
        s = _make_settings(
            secret_key="real-prod-secret-32chars-or-more",
            csrf_secret="real-prod-csrf-32chars-or-more",
        )
        assert s.secret_key == "real-prod-secret-32chars-or-more"

    def test_rejects_csrf_disabled_in_production(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _make_settings(
                secret_key="real-prod-secret-32chars-or-more",
                csrf_secret="real-prod-csrf-32chars-or-more",
                csrf_disabled=True,
            )
        assert "CSRF_DISABLED" in str(exc.value)


class TestCookieDomain:
    def test_cookie_domain_explicit_wins(self) -> None:
        s = Settings(cookie_domain=".zivo.fyi", environment="development")
        assert s.resolved_cookie_domain == "zivo.fyi"

    def test_cookie_domain_host_only_in_development(self) -> None:
        s = Settings(environment="development", frontend_url="https://zivo.fyi")
        assert s.resolved_cookie_domain is None

    def test_cookie_domain_derived_from_frontend_url_in_production(self) -> None:
        s = _make_settings(
            secret_key="real-prod-secret-32chars-or-more",
            csrf_secret="real-prod-csrf-32chars-or-more",
            frontend_url="https://zivo.fyi",
            cookie_domain="",
        )
        assert s.resolved_cookie_domain == "zivo.fyi"


class TestCorsOrigins:
    def test_includes_frontend_url_when_cors_origins_empty(self) -> None:
        s = _make_settings(
            secret_key="real-prod-secret-32chars-or-more",
            csrf_secret="real-prod-csrf-32chars-or-more",
            cors_origins="",
            frontend_url="https://zivo.fyi",
        )
        assert "https://zivo.fyi" in s.cors_origin_list
