"""Session bootstrap — CSRF restoration after reload."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models import User
from app.services.auth import create_session_token, csrf_from_session_token, get_current_user


def test_csrf_from_session_token_roundtrip() -> None:
    token, csrf = create_session_token(uuid.uuid4())
    assert csrf_from_session_token(token) == csrf


def test_get_session_returns_csrf_for_valid_cookie() -> None:
    token, csrf = create_session_token(uuid.uuid4())
    fake_user = User(username="benito", password_hash="hashed")

    app.dependency_overrides[get_current_user] = lambda: fake_user
    try:
        client = TestClient(app)
        client.cookies.set("citepage_session", token)
        res = client.get("/api/auth/session")
        assert res.status_code == 200
        body = res.json()
        assert body["username"] == "benito"
        assert body["csrf_token"] == csrf
    finally:
        app.dependency_overrides.clear()


def test_get_session_unauthenticated() -> None:
    client = TestClient(app)
    res = client.get("/api/auth/session")
    assert res.status_code == 401
