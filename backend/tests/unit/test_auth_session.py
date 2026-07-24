"""Session bootstrap — CSRF restoration after reload."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import User
from app.services.auth import create_session_token, csrf_from_session_token, get_optional_user


def test_csrf_from_session_token_roundtrip() -> None:
    token, csrf = create_session_token(uuid.uuid4())
    assert csrf_from_session_token(token) == csrf


def test_get_session_returns_csrf_for_valid_cookie() -> None:
    token, csrf = create_session_token(uuid.uuid4())
    # is_admin defaults at the DB layer (INSERT), not at instantiation — a User
    # built outside a session leaves it None, which AuthResponse's bool field
    # rejects. Set it explicitly so the test reflects a real (DB-loaded) user.
    fake_user = User(username="benito", password_hash="hashed", is_admin=False)

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
    # Anonymous visitors get a 200 with authenticated:false (not a 401), so the
    # browser console stays clean. See SessionResponse in app/api/auth.py.
    client = TestClient(app)
    res = client.get("/api/auth/session")
    assert res.status_code == 200
    body = res.json()
    assert body["authenticated"] is False
    assert body["username"] is None
    assert body["csrf_token"] is None


def test_logout_returns_204() -> None:
    import time

    from sqlalchemy import select

    from app.db import SessionLocal

    try:
        db = SessionLocal()
        db.execute(select(1))
        db.close()
    except Exception:
        pytest.skip("dev DB not reachable")

    username = f"logout_{int(time.time())}_{uuid.uuid4().hex[:6]}"

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
