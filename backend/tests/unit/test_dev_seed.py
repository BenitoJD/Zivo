"""Local dev user seeding guards and upsert behavior."""

import uuid
from unittest.mock import MagicMock

import pytest

from app.services.dev_seed import SeedUserSpec, assert_local_dev_seed_allowed, upsert_seed_user


def test_assert_local_dev_seed_allowed_rejects_non_development(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://zivo:zivo@localhost:5453/zivo")
    monkeypatch.setenv("SECRET_KEY", "real-prod-secret-32chars-or-more")
    monkeypatch.setenv("CSRF_SECRET", "real-prod-csrf-32chars-or-more")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "prod-access")
    monkeypatch.setenv("MINIO_SECRET_KEY", "prod-secret-value")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="development only"):
            assert_local_dev_seed_allowed()
    finally:
        get_settings.cache_clear()


def test_assert_local_dev_seed_allowed_rejects_remote_db(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://zivo:zivo@db.example.com:5432/zivo")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="non-local"):
            assert_local_dev_seed_allowed()
    finally:
        get_settings.cache_clear()


def test_upsert_seed_user_creates_then_updates() -> None:
    db = MagicMock()
    spec = SeedUserSpec("dev", "devpass1", False)
    admin_spec = SeedUserSpec("admin", "adminpass1", True)

    db.execute.return_value.first.return_value = None
    created_user = MagicMock()
    created_user.username = "dev"
    created_user.is_admin = False
    db.get.return_value = created_user

    user, created = upsert_seed_user(db, spec)
    assert created is True
    assert user.username == "dev"
    assert user.is_admin is False
    assert db.execute.call_count == 3
    db.flush.assert_called()

    existing_id = uuid.uuid4()
    db.reset_mock()
    db.execute.return_value.first.return_value = (existing_id,)
    existing_user = MagicMock()
    existing_user.username = "admin"
    existing_user.is_admin = True
    db.get.return_value = existing_user

    user, created = upsert_seed_user(db, admin_spec)
    assert created is False
    assert user is existing_user
    assert user.is_admin is True
    assert db.execute.call_count == 3
    db.flush.assert_called()
