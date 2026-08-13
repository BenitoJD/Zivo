"""Session bootstrap — CSRF restoration after reload."""

from __future__ import annotations

import base64
import json
import time
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.responses import Response

from app.main import app
from app.models import Account
from app.services.auth import (
    create_session_token,
    csrf_from_session_token,
    get_optional_user,
    set_session_cookie,
)


def test_csrf_from_session_token_roundtrip() -> None:
    token, csrf = create_session_token(uuid.uuid4())
    assert csrf_from_session_token(token) == csrf


def _jwt_payload(token: str) -> dict:
    parts = token.split(".")
    pad = "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(parts[1] + pad))


def test_remember_token_exp_matches_remember_cookie_ttl() -> None:
    token_short, _ = create_session_token(uuid.uuid4(), remember=False)
    token_long, _ = create_session_token(uuid.uuid4(), remember=True)
    short_days = (_jwt_payload(token_short)["exp"] - time.time()) / 86400
    long_days = (_jwt_payload(token_long)["exp"] - time.time()) / 86400
    assert 6.5 < short_days < 7.5
    assert 29.5 < long_days < 30.5


def test_set_session_cookie_sets_shared_domain_when_configured() -> None:
    response = Response()
    token, _ = create_session_token(uuid.uuid4(), remember=True)
    with patch("app.services.auth.settings") as mock_settings:
        mock_settings.is_production = True
        mock_settings.session_days = 7
        mock_settings.session_remember_days = 30
        mock_settings.resolved_cookie_domain = "zivo.fyi"
        set_session_cookie(response, token, remember=True)
    header = response.headers.get("set-cookie", "")
    assert "zivo_session=" in header
    assert "Domain=zivo.fyi" in header
    assert "Max-Age=2592000" in header
    assert "HttpOnly" in header
    assert "samesite=lax" in header.lower()
    assert "secure" in header.lower()


def test_set_session_cookie_host_only_without_domain() -> None:
    response = Response()
    token, _ = create_session_token(uuid.uuid4(), remember=False)
    with patch("app.services.auth.settings") as mock_settings:
        mock_settings.is_production = False
        mock_settings.session_days = 7
        mock_settings.session_remember_days = 30
        mock_settings.resolved_cookie_domain = None
        set_session_cookie(response, token, remember=False)
    header = response.headers.get("set-cookie", "")
    assert "Domain=" not in header
    assert "Max-Age=604800" in header


def test_get_session_returns_csrf_for_valid_cookie() -> None:
    token, csrf = create_session_token(uuid.uuid4())
    fake_user = Account(username="benito", password_hash="hashed", is_admin=False)

    app.dependency_overrides[get_optional_user] = lambda: fake_user
    try:
        client = TestClient(app)
        client.cookies.set("zivo_session", token)
        res = client.get("/api/auth/session")
        assert res.status_code == 200
        body = res.json()
        assert body["authenticated"] is True
        assert body["username"] == "benito"
        assert body["csrf_token"] == csrf
    finally:
        app.dependency_overrides.clear()


def test_get_session_unauthenticated() -> None:
    client = TestClient(app)
    res = client.get("/api/auth/session")
    assert res.status_code == 200
    body = res.json()
    assert body["authenticated"] is False
    assert body["username"] is None
    assert body["csrf_token"] is None


def test_logout_returns_204() -> None:
    import time as time_mod

    from sqlalchemy import select, text

    from app.db import SessionLocal

    try:
        db = SessionLocal()
        db.execute(select(1))
        db.execute(text("SELECT 1 FROM auth.account LIMIT 1"))
        db.close()
    except Exception:
        pytest.skip("dev DB not reachable or auth.account not migrated")

    username = f"logout_{int(time_mod.time())}_{uuid.uuid4().hex[:6]}"

    client = TestClient(app)
    signup = client.post(
        "/api/auth/signup",
        json={
            "username": username,
            "password": "SmokeTest1!",
            "confirm_password": "SmokeTest1!",
            "accept_terms": True,
        },
    )
    assert signup.status_code == 200, signup.text
    csrf = signup.json()["csrf_token"]
    out = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert out.status_code == 204, out.text

    session = client.get("/api/auth/session")
    assert session.json()["authenticated"] is False
