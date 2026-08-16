from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, Header, HTTPException, Response
from jose import JWTError, jwt
import bcrypt
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick
from app.models import Account

settings = get_settings()

USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{2,31}$")


def _raise(exc: BaseException) -> None:
    raise exc


def _raise_from(cause: BaseException, wrapped: BaseException) -> None:
    raise wrapped from cause


def validate_username(username: str) -> None:
    pick(
        not USERNAME_RE.match(username),
        lambda: _raise(HTTPException(status_code=400, detail="Invalid username format")),
        lambda: None,
    )


def validate_password(password: str) -> None:
    pick(
        len(password) < 8,
        lambda: _raise(HTTPException(status_code=400, detail="Password too short")),
        lambda: None,
    )
    pick(
        not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password),
        lambda: _raise(
            HTTPException(status_code=400, detail="Password must include a letter and a number")
        ),
        lambda: None,
    )


def _password_bytes(password: str) -> bytes:
    raw = password.encode("utf-8")
    return pick(len(raw) > 72, lambda: hashlib.sha256(raw).digest(), lambda: raw)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(_password_bytes(password), password_hash.encode("utf-8"))


_DUMMY_HASH = bcrypt.hashpw(b"timing-oracle-guard", bcrypt.gensalt()).decode("utf-8")


def create_session_token(
    account_id: uuid.UUID,
    session_version: int = 0,
    *,
    remember: bool = False,
) -> tuple[str, str]:
    days = choose(remember, settings.session_remember_days, settings.session_days)
    exp = datetime.now(timezone.utc) + timedelta(days=days)
    csrf = secrets.token_urlsafe(32)
    payload = {"sub": str(account_id), "exp": exp, "csrf": csrf, "sv": int(session_version)}
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    return token, csrf


def _session_version_matches(user: Account, payload: dict) -> bool:
    token_sv = payload.get("sv", 0)
    try:
        token_sv = int(token_sv)
    except (TypeError, ValueError):
        token_sv = 0
    user_sv = int(getattr(user, "session_version", 0) or 0)
    return token_sv == user_sv


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
    """delete_cookie rejects max_age; keep Domain/Path/Secure/SameSite aligned."""
    kwargs = cookie_set_kwargs()
    kwargs.pop("max_age", None)
    return kwargs


def set_session_cookie(response: Response, token: str, remember: bool) -> None:
    max_age = choose(remember, settings.session_remember_days, settings.session_days) * 86400
    response.set_cookie(key="zivo_session", value=token, **cookie_set_kwargs(max_age=max_age))


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie("zivo_session", **cookie_delete_kwargs())


def get_current_user(
    db: Session = Depends(get_db),
    zivo_session: str | None = Cookie(default=None),
) -> Account:
    pick(
        not zivo_session,
        lambda: _raise(HTTPException(status_code=401, detail="Not authenticated")),
        lambda: None,
    )
    try:
        payload = jwt.decode(zivo_session, settings.secret_key, algorithms=["HS256"])
        account_id = uuid.UUID(payload["sub"])
    except (JWTError, ValueError) as exc:
        _raise_from(exc, HTTPException(status_code=401, detail="Invalid session"))

    user = db.get(Account, account_id)
    pick(
        not user,
        lambda: _raise(HTTPException(status_code=401, detail="User not found")),
        lambda: None,
    )
    pick(
        not _session_version_matches(user, payload),
        lambda: _raise(HTTPException(status_code=401, detail="Session revoked")),
        lambda: None,
    )
    return user


def get_optional_user(
    db: Session = Depends(get_db),
    zivo_session: str | None = Cookie(default=None),
) -> Account | None:
    def _decode() -> Account | None:
        try:
            payload = jwt.decode(zivo_session, settings.secret_key, algorithms=["HS256"])
            account_id = uuid.UUID(payload["sub"])
        except (JWTError, ValueError):
            return None
        user = db.get(Account, account_id)
        return pick(
            not user or not _session_version_matches(user, payload),
            lambda: None,
            lambda: user,
        )

    return pick(not zivo_session, lambda: None, _decode)


def csrf_from_session_token(zivo_session: str) -> str:
    try:
        payload = jwt.decode(zivo_session, settings.secret_key, algorithms=["HS256"])
    except JWTError as exc:
        _raise_from(exc, HTTPException(status_code=401, detail="Invalid session"))
    csrf = payload.get("csrf")
    pick(
        not isinstance(csrf, str) or not csrf,
        lambda: _raise(HTTPException(status_code=401, detail="Invalid session")),
        lambda: None,
    )
    return csrf


def require_csrf(
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    zivo_session: str | None = Cookie(default=None),
) -> None:
    def _check() -> None:
        apply(
            first_match(
                (
                    Rule(when=(Pred("session", "falsey"),), action="required"),
                    Rule(when=(Pred("header", "falsey"),), action="required"),
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
        try:
            payload = jwt.decode(zivo_session, settings.secret_key, algorithms=["HS256"])
            pick(
                payload.get("csrf") != x_csrf_token,
                lambda: _raise(HTTPException(status_code=403, detail="CSRF mismatch")),
                lambda: None,
            )
        except JWTError as exc:
            _raise_from(exc, HTTPException(status_code=403, detail="Invalid CSRF"))

    pick(settings.csrf_disabled, lambda: None, _check)


def authenticate_user(db: Session, username: str, password: str) -> Account | None:
    user = db.query(Account).filter(Account.username == username).first()

    def _dummy() -> Account | None:
        bcrypt.checkpw(b"timing-oracle-guard", _DUMMY_HASH.encode("utf-8"))
        return None

    def _verify() -> Account | None:
        return pick(not verify_password(password, user.password_hash), lambda: None, lambda: user)

    return pick(not user or not user.password_hash, _dummy, _verify)
