"""Configuration validation: dev secrets must be rejected outside development."""

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

    def test_accepts_vps_minio_bootstrap_keys_in_production(self) -> None:
        s = _make_settings(
            secret_key="real-prod-secret-32chars-or-more",
            csrf_secret="real-prod-csrf-32chars-or-more",
            minio_access_key="zivo",
            minio_secret_key="zivo-secret",
        )
        assert s.minio_access_key == "zivo"

    def test_accepts_unique_secrets_in_production(self) -> None:
        s = _make_settings(
            secret_key="real-prod-secret-32chars-or-more",
            csrf_secret="real-prod-csrf-32chars-or-more",
            minio_access_key="prod-access",
            minio_secret_key="prod-secret-value",
        )
        assert s.secret_key == "real-prod-secret-32chars-or-more"
        assert s.csrf_secret == "real-prod-csrf-32chars-or-more"

    def test_rejects_csrf_disabled_in_production(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _make_settings(
                secret_key="real-prod-secret-32chars-or-more",
                csrf_secret="real-prod-csrf-32chars-or-more",
                minio_access_key="prod-access",
                minio_secret_key="prod-secret-value",
                csrf_disabled=True,
            )
        assert "CSRF_DISABLED" in str(exc.value)


class TestSettingsAccessors:
    def test_cors_origin_list_parses_csv(self) -> None:
        s = Settings(cors_origins="https://a.com, https://b.com,, ")
        assert s.cors_origin_list == ["https://a.com", "https://b.com"]

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
            minio_access_key="prod-access",
            minio_secret_key="prod-secret-value",
            frontend_url="https://zivo.fyi",
            cookie_domain="",
        )
        assert s.resolved_cookie_domain == "zivo.fyi"

    def test_cookie_domain_derived_strips_www(self) -> None:
        s = _make_settings(
            secret_key="real-prod-secret-32chars-or-more",
            csrf_secret="real-prod-csrf-32chars-or-more",
            minio_access_key="prod-access",
            minio_secret_key="prod-secret-value",
            frontend_url="https://www.zivo.fyi",
            cookie_domain="",
        )
        assert s.resolved_cookie_domain == "zivo.fyi"

    def test_minio_presign_endpoint_falls_back_to_internal(self) -> None:
        s = Settings()
        assert s.minio_presign_endpoint == s.minio_endpoint

    def test_minio_presign_endpoint_uses_public_when_set(self) -> None:
        s = Settings(minio_public_endpoint="s3.example.com")
        assert s.minio_presign_endpoint == "s3.example.com"

    def test_minio_presign_secure_falls_back_to_internal(self) -> None:
        s = Settings()
        assert s.minio_presign_secure is False

    def test_minio_presign_secure_uses_public_when_set(self) -> None:
        s = Settings(minio_public_secure=True)
        assert s.minio_presign_secure is True
