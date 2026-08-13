from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Cookie, Depends, Header, HTTPException, Response
from jose import JWTError, jwt
import bcrypt
from sqlalchemy.orm import Session
from sqlalchemy.sql import text

from app.config import get_settings
from app.db import get_db
from app.models import Account, User
from app.services.usage import DEMO_COOKIE

settings = get_settings()


def _password_bytes(password: str) -> bytes:
    raw = password.encode("utf-8")
    if len(raw) > 72:
        return hashlib.sha256(raw).digest()
    return raw


def hash_password(password: str) -> str:
    """Kept for local seed; the auth service is the only runtime hasher."""
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(_password_bytes(password), password_hash.encode("utf-8"))


def create_session_token(
    account_id: uuid.UUID,
    session_version: int = 0,
    *,
    remember: bool = False,
) -> tuple[str, str]:
    """Mint a session JWT. Production minting lives on the auth service; tests reuse this."""
    days = settings.session_remember_days if remember else settings.session_days
    exp = datetime.now(timezone.utc) + timedelta(days=days)
    csrf = secrets.token_urlsafe(32)
    payload = {"sub": str(account_id), "exp": exp, "csrf": csrf, "sv": int(session_version)}
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    return token, csrf


def cookie_set_kwargs(*, max_age: int | None = None) -> dict:
    """Shared cookie flags so set + delete agree (Domain especially)."""
    kwargs: dict = {
        "httponly": True,
        "secure": settings.is_production,
        "samesite": "lax",
        "path": "/",
    }
    if max_age is not None:
        kwargs["max_age"] = max_age
    domain = settings.resolved_cookie_domain
    if domain:
        kwargs["domain"] = domain
    return kwargs


def cookie_delete_kwargs() -> dict:
    kwargs = cookie_set_kwargs()
    kwargs.pop("max_age", None)
    return kwargs


def set_session_cookie(response: Response, token: str, remember: bool) -> None:
    max_age = (settings.session_remember_days if remember else settings.session_days) * 86400
    response.set_cookie(key="zivo_session", value=token, **cookie_set_kwargs(max_age=max_age))


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie("zivo_session", **cookie_delete_kwargs())


def _decode_session(zivo_session: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(zivo_session, settings.secret_key, algorithms=["HS256"])
    except (JWTError, ValueError):
        return None


def _load_identity(db: Session, account_id: uuid.UUID) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT id, username, is_admin, session_version
            FROM auth.account
            WHERE id = :id
            """
        ),
        {"id": account_id},
    ).mappings().first()
    return dict(row) if row else None


def _session_version_matches(identity: dict[str, Any], payload: dict) -> bool:
    token_sv = payload.get("sv", 0)
    try:
        token_sv = int(token_sv)
    except (TypeError, ValueError):
        token_sv = 0
    user_sv = int(identity.get("session_version") or 0)
    return token_sv == user_sv


def _upsert_account_stub(db: Session, identity: dict[str, Any]) -> Account:
    account_id = identity["id"]
    db.execute(
        text(
            """
            INSERT INTO qb.account (id, username, is_admin)
            VALUES (:id, :username, :is_admin)
            ON CONFLICT (id) DO UPDATE SET
              username = EXCLUDED.username,
              is_admin = EXCLUDED.is_admin
            """
        ),
        {
            "id": account_id,
            "username": identity["username"],
            "is_admin": bool(identity["is_admin"]),
        },
    )
    db.flush()
    user = db.get(Account, account_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def _maybe_claim_guest(db: Session, user: Account, guest_id: str | None) -> None:
    if not guest_id:
        return
    from app.services.guest import claim_guest_documents, claim_guest_progress
    from app.services.guest_session import normalize_guest_id

    normalized = normalize_guest_id(guest_id)
    if not normalized:
        return
    claim_guest_documents(db, user.id, normalized)
    claim_guest_progress(db, user.id, user.username, normalized)


def _user_from_session(
    db: Session,
    zivo_session: str | None,
    *,
    required: bool,
    guest_id: str | None = None,
) -> Account | None:
    if not zivo_session:
        if required:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return None
    payload = _decode_session(zivo_session)
    if payload is None:
        if required:
            raise HTTPException(status_code=401, detail="Invalid session")
        return None
    try:
        account_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        if required:
            raise HTTPException(status_code=401, detail="Invalid session") from exc
        return None
    identity = _load_identity(db, account_id)
    if not identity:
        if required:
            raise HTTPException(status_code=401, detail="User not found")
        return None
    if not _session_version_matches(identity, payload):
        if required:
            raise HTTPException(status_code=401, detail="Session revoked")
        return None
    user = _upsert_account_stub(db, identity)
    _maybe_claim_guest(db, user, guest_id)
    return user


def get_current_user(
    db: Session = Depends(get_db),
    zivo_session: str | None = Cookie(default=None),
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
) -> User:
    user = _user_from_session(db, zivo_session, required=True, guest_id=zivo_demo_id)
    assert user is not None
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin required")
    return user


def get_optional_user(
    db: Session = Depends(get_db),
    zivo_session: str | None = Cookie(default=None),
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
) -> Account | None:
    return _user_from_session(db, zivo_session, required=False, guest_id=zivo_demo_id)


def csrf_from_session_token(zivo_session: str) -> str:
    payload = _decode_session(zivo_session)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid session")
    csrf = payload.get("csrf")
    if not isinstance(csrf, str) or not csrf:
        raise HTTPException(status_code=401, detail="Invalid session")
    return csrf


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
    """Signed-in users need CSRF; anonymous guests use httponly cookie."""
    if zivo_session:
        require_csrf(x_csrf_token, zivo_session)
        return
    return
