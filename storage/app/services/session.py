"""Duplicate JWT decode + session_version read. No shared Python package."""

from __future__ import annotations

import hmac
import uuid
from typing import Any

from fastapi import Cookie, Depends, Header, HTTPException
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from sqlalchemy.sql import text

from app.config import get_settings
from app.db import get_db
from app.engine_runtime import Pred, Rule, apply, first_match, pick
from app.services.guest import DEMO_COOKIE, GUEST_ID_HEADER, Principal, read_guest_id

settings = get_settings()


def _raise(exc: BaseException) -> None:
    raise exc


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
    return pick(row is None, lambda: None, lambda: int(row[0] or 0))


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
    anonymous = Principal(account_id=None, guest_id=guest_id)

    def _from_session() -> Principal:
        payload = _decode_session(zivo_session)

        def _from_payload() -> Principal:
            try:
                account_id = uuid.UUID(payload["sub"])
            except (KeyError, ValueError):
                return anonymous
            user_sv = _load_session_version(db, account_id)
            return pick(
                user_sv is None or not _session_version_matches(user_sv, payload),
                lambda: anonymous,
                lambda: Principal(account_id=account_id, guest_id=guest_id),
            )

        return pick(payload is None, lambda: anonymous, _from_payload)

    return pick(not zivo_session, lambda: anonymous, _from_session)


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
        payload = _decode_session(zivo_session)
        pick(
            payload is None,
            lambda: _raise(HTTPException(status_code=403, detail="Invalid CSRF")),
            lambda: None,
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
    pick(bool(zivo_session), lambda: require_csrf(x_csrf_token, zivo_session), lambda: None)


def require_internal_key(
    x_zivo_internal_key: str | None = Header(default=None, alias="X-Zivo-Internal-Key"),
) -> None:
    expected = settings.secret_key.encode("utf-8")
    given = (x_zivo_internal_key or "").encode("utf-8")
    pick(
        not given or not hmac.compare_digest(given, expected),
        lambda: _raise(HTTPException(status_code=401, detail="Invalid internal key")),
        lambda: None,
    )
