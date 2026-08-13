"""Google OAuth auth routes — start, callback pending, status."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.google_oauth import mint_pending_token, read_pending_token


def test_google_status_disabled_without_credentials(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")
    get_settings.cache_clear()
    try:
        client = TestClient(app)
        res = client.get("/api/auth/google/status")
        assert res.status_code == 200
        assert res.json() == {"enabled": False}
    finally:
        get_settings.cache_clear()


def test_google_start_requires_config(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")
    get_settings.cache_clear()
    try:
        client = TestClient(app)
        res = client.get("/api/auth/google", follow_redirects=False)
        assert res.status_code == 503
    finally:
        get_settings.cache_clear()


def test_google_start_redirects_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:3000/api/auth/google/callback")
    get_settings.cache_clear()
    try:
        client = TestClient(app)
        res = client.get("/api/auth/google", follow_redirects=False)
        assert res.status_code == 302
        assert "accounts.google.com" in res.headers["location"]
        assert "client_id=test-client-id" in res.headers["location"]
        assert "zivo_google_oauth_state" in res.cookies
    finally:
        get_settings.cache_clear()


def test_google_callback_new_user_sets_pending(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    get_settings.cache_clear()
    try:
        client = TestClient(app)
        start = client.get("/api/auth/google", follow_redirects=False)
        state = start.cookies.get("zivo_google_oauth_state")
        assert state

        empty = type("Q", (), {"first": staticmethod(lambda: None)})()
        fake_db = type(
            "DB",
            (),
            {"query": lambda self, _model: type("Chain", (), {"filter": lambda self, *_a, **_k: empty})()},
        )()

        with patch(
            "app.api.auth.exchange_code_for_userinfo",
            return_value={"google_sub": "google-sub-1", "email": "ada@example.com"},
        ):
            from app.db import get_db

            def _override_db():
                yield fake_db

            app.dependency_overrides[get_db] = _override_db
            try:
                res = client.get(
                    f"/api/auth/google/callback?code=fake&state={state}",
                    follow_redirects=False,
                )
            finally:
                app.dependency_overrides.pop(get_db, None)

        assert res.status_code == 302
        assert res.headers["location"].endswith("/signup/google")
        assert "zivo_google_pending" in res.cookies
    finally:
        get_settings.cache_clear()


def test_google_callback_rejects_bad_state(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    get_settings.cache_clear()
    try:
        client = TestClient(app)
        res = client.get(
            "/api/auth/google/callback?code=fake&state=wrong",
            follow_redirects=False,
        )
        assert res.status_code == 302
        assert "login?error=" in res.headers["location"]
    finally:
        get_settings.cache_clear()


def test_pending_token_roundtrip() -> None:
    get_settings.cache_clear()
    settings = get_settings()
    token = mint_pending_token(settings, google_sub="sub-9", email="Ada@Example.COM")
    sub, email = read_pending_token(settings, token)
    assert sub == "sub-9"
    assert email == "ada@example.com"


def test_google_complete_requires_pending_cookie() -> None:
    client = TestClient(app)
    res = client.post(
        "/api/auth/google/complete",
        json={"username": "gracehopper", "accept_terms": True},
    )
    assert res.status_code == 401
