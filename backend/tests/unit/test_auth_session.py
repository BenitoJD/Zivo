"""Session cookie helpers still used by the product API (guest cookies, CSRF)."""

from __future__ import annotations

import base64
import json
import time
import uuid
from unittest.mock import patch

from starlette.responses import Response

from app.services.auth import (
    create_session_token,
    csrf_from_session_token,
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
