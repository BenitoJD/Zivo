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
from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.models import Account, User
from app.services.presence import evaluate_presence
from app.services.usage import DEMO_COOKIE

settings = get_settings()


def _raise(exc: BaseException) -> None:
    raise exc


def _raise_from(cause: BaseException, wrapped: BaseException) -> None:
    raise wrapped from cause


def _password_bytes(password: str) -> bytes:
    raw = password.encode("utf-8")
    return pick(len(raw) > 72, lambda: hashlib.sha256(raw).digest(), lambda: raw)


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
    days = choose(remember, settings.session_remember_days, settings.session_days)
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
    pick(max_age is not None, lambda: kwargs.__setitem__("max_age", max_age), lambda: None)
    domain = settings.resolved_cookie_domain
    pick(bool(domain), lambda: kwargs.__setitem__("domain", domain), lambda: None)
    return kwargs


def cookie_delete_kwargs() -> dict:
    kwargs = cookie_set_kwargs()
    kwargs.pop("max_age", None)
    return kwargs


def set_session_cookie(response: Response, token: str, remember: bool) -> None:
    max_age = choose(remember, settings.session_remember_days, settings.session_days) * 86400
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
    return apply(
        evaluate_presence(row).action,
        {
            "missing": lambda: None,
            "empty": lambda: None,
            "ok": lambda: dict(row),
        },
    )


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
    apply(
        evaluate_presence(user).action,
        {
            "missing": lambda: _raise(HTTPException(status_code=401, detail="User not found")),
            "empty": lambda: _raise(HTTPException(status_code=401, detail="User not found")),
            "ok": lambda: None,
        },
    )
    return user


def _maybe_claim_guest(db: Session, user: Account, guest_id: str | None) -> None:
    def _claim() -> None:
        from app.services.guest import claim_guest_documents, claim_guest_progress
        from app.services.guest_session import normalize_guest_id

        normalized = normalize_guest_id(guest_id)
        pick(
            not normalized,
            lambda: None,
            lambda: (
                claim_guest_documents(db, user.id, normalized),
                claim_guest_progress(db, user.id, user.username, normalized),
            ),
        )

    apply(
        evaluate_presence(guest_id).action,
        {
            "missing": lambda: None,
            "empty": lambda: None,
            "ok": _claim,
        },
    )


def _unauth(required: bool, detail: str, cause: BaseException | None = None) -> Account | None:
    def _do_raise() -> Account | None:
        exc = HTTPException(status_code=401, detail=detail)
        return pick(
            cause is None,
            lambda: _raise(exc),
            lambda: _raise_from(cause, exc),
        )

    return pick(required, _do_raise, lambda: None)


def _user_from_session(
    db: Session,
    zivo_session: str | None,
    *,
    required: bool,
    guest_id: str | None = None,
) -> Account | None:
    def _after_cookie() -> Account | None:
        payload = _decode_session(zivo_session)

        def _after_payload() -> Account | None:
            try:
                account_id = uuid.UUID(payload["sub"])
            except (KeyError, ValueError) as exc:
                return _unauth(required, "Invalid session", exc)

            identity = _load_identity(db, account_id)

            def _after_identity() -> Account | None:
                def _after_sv() -> Account:
                    user = _upsert_account_stub(db, identity)
                    _maybe_claim_guest(db, user, guest_id)
                    return user

                return pick(
                    not _session_version_matches(identity, payload),
                    lambda: _unauth(required, "Session revoked"),
                    _after_sv,
                )

            return apply(
                evaluate_presence(identity).action,
                {
                    "missing": lambda: _unauth(required, "User not found"),
                    "empty": lambda: _unauth(required, "User not found"),
                    "ok": _after_identity,
                },
            )

        return apply(
            evaluate_presence(payload).action,
            {
                "missing": lambda: _unauth(required, "Invalid session"),
                "empty": lambda: _unauth(required, "Invalid session"),
                "ok": _after_payload,
            },
        )

    return apply(
        evaluate_presence(zivo_session).action,
        {
            "missing": lambda: _unauth(required, "Not authenticated"),
            "empty": lambda: _unauth(required, "Not authenticated"),
            "ok": _after_cookie,
        },
    )


def get_current_user(
    db: Session = Depends(get_db),
    zivo_session: str | None = Cookie(default=None),
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
) -> User:
    user = _user_from_session(db, zivo_session, required=True, guest_id=zivo_demo_id)
    assert user is not None
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    apply(
        first_match(
            (
                Rule(when=(Pred("admin", "falsey"),), action="deny"),
                Rule(when=(), action="allow"),
            ),
            {"admin": user.is_admin},
        ).action,
        {
            "deny": lambda: _raise(HTTPException(status_code=403, detail="Admin required")),
            "allow": lambda: None,
        },
    )
    return user


def get_optional_user(
    db: Session = Depends(get_db),
    zivo_session: str | None = Cookie(default=None),
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
) -> Account | None:
    return _user_from_session(db, zivo_session, required=False, guest_id=zivo_demo_id)


def csrf_from_session_token(zivo_session: str) -> str:
    payload = _decode_session(zivo_session)
    apply(
        evaluate_presence(payload).action,
        {
            "missing": lambda: _raise(HTTPException(status_code=401, detail="Invalid session")),
            "empty": lambda: _raise(HTTPException(status_code=401, detail="Invalid session")),
            "ok": lambda: None,
        },
    )
    csrf = payload.get("csrf")
    csrf_str = pick(isinstance(csrf, str), lambda: csrf, lambda: None)
    return apply(
        evaluate_presence(csrf_str).action,
        {
            "missing": lambda: _raise(HTTPException(status_code=401, detail="Invalid session")),
            "empty": lambda: _raise(HTTPException(status_code=401, detail="Invalid session")),
            "ok": lambda: csrf_str,
        },
    )


def require_csrf(
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    zivo_session: str | None = Cookie(default=None),
) -> None:
    def _check() -> None:
        apply(
            first_match(
                (
                    Rule(
                        when=(Pred("session", "falsey"),),
                        action="required",
                    ),
                    Rule(
                        when=(Pred("header", "falsey"),),
                        action="required",
                    ),
                    Rule(when=(), action="compare"),
                ),
                {"session": zivo_session, "header": x_csrf_token},
            ).action,
            {
                "required": lambda: _raise(
                    HTTPException(status_code=403, detail="CSRF token required")
                ),
                "compare": lambda: None,
            },
        )
        payload = _decode_session(zivo_session)
        apply(
            evaluate_presence(payload).action,
            {
                "missing": lambda: _raise(HTTPException(status_code=403, detail="Invalid CSRF")),
                "empty": lambda: _raise(HTTPException(status_code=403, detail="Invalid CSRF")),
                "ok": lambda: None,
            },
        )
        pick(
            payload.get("csrf") != x_csrf_token,
            lambda: _raise(HTTPException(status_code=403, detail="CSRF mismatch")),
            lambda: None,
        )

    pick(settings.csrf_disabled, lambda: None, _check)


def require_csrf_or_guest(
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    zivo_session: str | None = Cookie(default=None),
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
) -> None:
    """Signed-in users need CSRF; anonymous guests use httponly cookie."""
    apply(
        evaluate_presence(zivo_session).action,
        {
            "ok": lambda: require_csrf(x_csrf_token, zivo_session),
            "missing": lambda: None,
            "empty": lambda: None,
        },
    )
