"""Google OAuth authorization-code helpers.

Flow:
  1. GET /api/auth/google → redirect to Google (state cookie)
  2. GET /api/auth/google/callback → exchange code; existing user → session;
     new user → pending JWT cookie → frontend username form
  3. POST /api/auth/google/complete → create account + session
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException
from jose import JWTError, jwt

from app.config import Settings

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

PENDING_COOKIE = "zivo_google_pending"
STATE_COOKIE = "zivo_google_oauth_state"
PENDING_TTL_MINUTES = 15
PENDING_COOKIE_MAX_AGE = PENDING_TTL_MINUTES * 60


def google_oauth_configured(settings: Settings) -> bool:
    return bool(settings.google_client_id.strip() and settings.google_client_secret.strip())


def require_google_oauth(settings: Settings) -> None:
    if not google_oauth_configured(settings):
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")


def build_google_authorize_url(settings: Settings, state: str) -> str:
    require_google_oauth(settings)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "include_granted_scopes": "true",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def new_oauth_state() -> str:
    return secrets.token_urlsafe(32)


def mint_pending_token(settings: Settings, *, google_sub: str, email: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=PENDING_TTL_MINUTES)
    payload = {
        "typ": "google_pending",
        "sub": google_sub,
        "email": email,
        "exp": exp,
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def read_pending_token(settings: Settings, token: str) -> tuple[str, str]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Google signup expired — try again") from exc
    if payload.get("typ") != "google_pending":
        raise HTTPException(status_code=401, detail="Invalid Google signup token")
    google_sub = payload.get("sub")
    email = payload.get("email")
    if not isinstance(google_sub, str) or not google_sub:
        raise HTTPException(status_code=401, detail="Invalid Google signup token")
    if not isinstance(email, str) or not email:
        raise HTTPException(status_code=401, detail="Google account has no email")
    return google_sub, email.lower()


def exchange_code_for_userinfo(settings: Settings, code: str) -> dict[str, Any]:
    """Exchange auth code → access token → OpenID userinfo. Sync httpx (auth routes are sync)."""
    require_google_oauth(settings)
    try:
        token_res = httpx.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=20.0,
        )
        token_res.raise_for_status()
        token_body = token_res.json()
        access_token = token_body.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise HTTPException(status_code=502, detail="Google token response missing access_token")

        info_res = httpx.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20.0,
        )
        info_res.raise_for_status()
        info = info_res.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Google authentication failed") from exc

    google_sub = info.get("sub")
    email = info.get("email")
    if not isinstance(google_sub, str) or not google_sub:
        raise HTTPException(status_code=502, detail="Google profile missing subject")
    if not isinstance(email, str) or not email:
        raise HTTPException(status_code=400, detail="Google account must share an email")
    if info.get("email_verified") is False:
        raise HTTPException(status_code=400, detail="Google email is not verified")
    return {"google_sub": google_sub, "email": email.lower()}


def frontend_path(settings: Settings, path: str) -> str:
    base = settings.frontend_url.rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"
