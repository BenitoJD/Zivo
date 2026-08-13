"""Configuration validation for the storage service."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def _make_settings(**overrides: object) -> Settings:
    base = {
        "secret_key": "dev-secret-change-me",
        "csrf_secret": "dev-csrf-change-me",
        "environment": "production",
        "minio_access_key": "prod-access",
        "minio_secret_key": "prod-secret-value",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class TestSecretKeyValidation:
    def test_rejects_dev_secret_key_in_production(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _make_settings(minio_access_key="prod-access", minio_secret_key="prod-secret-value")
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

    def test_minio_presign_endpoint_falls_back_to_internal(self) -> None:
        s = Settings(minio_endpoint="minio.internal:9000")
        assert s.minio_presign_endpoint == "minio.internal:9000"

    def test_minio_presign_endpoint_uses_public_when_set(self) -> None:
        s = Settings(minio_public_endpoint="s3.example.com")
        assert s.minio_presign_endpoint == "s3.example.com"

    def test_minio_presign_secure_uses_public_when_set(self) -> None:
        s = Settings(minio_public_secure=True)
        assert s.minio_presign_secure is True
