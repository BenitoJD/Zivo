"""Duplicate JWT decode + session_version read. No shared Python package."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Cookie, Depends, Header, HTTPException
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from sqlalchemy.sql import text

from app.config import get_settings
from app.db import get_db
from app.services.guest import DEMO_COOKIE, GUEST_ID_HEADER, Principal, read_guest_id

settings = get_settings()


def _decode_session(zivo_session: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(zivo_session, settings.secret_key, algorithms=["HS256"])
    except (JWTError, ValueError):
        return None


def _load_session_version(db: Session, account_id: uuid.UUID) -> int | None:
    row = db.execute(
        text("SELECT session_version FROM auth.account WHERE id = :id"),
        {"id": account_id},
    ).first()
    if row is None:
        return None
    return int(row[0] or 0)


def _session_version_matches(user_sv: int, payload: dict) -> bool:
    token_sv = payload.get("sv", 0)
    try:
        token_sv = int(token_sv)
    except (TypeError, ValueError):
        token_sv = 0
    return token_sv == user_sv


def optional_principal(
    db: Session = Depends(get_db),
    zivo_session: str | None = Cookie(default=None),
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
    x_zivo_guest_id: str | None = Header(default=None, alias=GUEST_ID_HEADER),
) -> Principal:
    guest_id = read_guest_id(zivo_demo_id, x_zivo_guest_id)
    if not zivo_session:
        return Principal(account_id=None, guest_id=guest_id)
    payload = _decode_session(zivo_session)
    if payload is None:
        return Principal(account_id=None, guest_id=guest_id)
    try:
        account_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        return Principal(account_id=None, guest_id=guest_id)
    user_sv = _load_session_version(db, account_id)
    if user_sv is None:
        return Principal(account_id=None, guest_id=guest_id)
    if not _session_version_matches(user_sv, payload):
        return Principal(account_id=None, guest_id=guest_id)
    return Principal(account_id=account_id, guest_id=guest_id)


def require_csrf(
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    zivo_session: str | None = Cookie(default=None),
) -> None:
    if settings.csrf_disabled:
        return
    if not zivo_session or not x_csrf_token:
        raise HTTPException(status_code=403, detail="CSRF token required")
    payload = _decode_session(zivo_session)
    if payload is None:
        raise HTTPException(status_code=403, detail="Invalid CSRF")
    if payload.get("csrf") != x_csrf_token:
        raise HTTPException(status_code=403, detail="CSRF mismatch")


def require_csrf_or_guest(
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    zivo_session: str | None = Cookie(default=None),
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
) -> None:
    if zivo_session:
        require_csrf(x_csrf_token, zivo_session)
        return
    return


def require_internal_key(
    x_zivo_internal_key: str | None = Header(default=None, alias="X-Zivo-Internal-Key"),
) -> None:
    import hmac

    expected = settings.secret_key.encode("utf-8")
    given = (x_zivo_internal_key or "").encode("utf-8")
    if not given or not hmac.compare_digest(given, expected):
        raise HTTPException(status_code=401, detail="Invalid internal key")
