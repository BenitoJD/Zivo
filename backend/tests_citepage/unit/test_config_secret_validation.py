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

    def test_accepts_unique_secrets_in_production(self) -> None:
        s = _make_settings(
            secret_key="real-prod-secret-32chars-or-more",
            csrf_secret="real-prod-csrf-32chars-or-more",
        )
        assert s.secret_key == "real-prod-secret-32chars-or-more"
        assert s.csrf_secret == "real-prod-csrf-32chars-or-more"

    def test_allows_dev_secrets_in_development(self) -> None:
        s = _make_settings(environment="development")
        assert s.secret_key == "dev-secret-change-me"


class TestSettingsAccessors:
    def test_cors_origin_list_parses_csv(self) -> None:
        s = Settings(cors_origins="https://a.com, https://b.com,, ")
        assert s.cors_origin_list == ["https://a.com", "https://b.com"]

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
