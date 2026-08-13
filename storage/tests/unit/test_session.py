"""JWT session decode and internal key gate."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from jose import jwt

from app.config import get_settings
from app.services.session import _decode_session, optional_principal, require_internal_key


def _token(account_id: uuid.UUID, session_version: int = 0) -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=1)
    payload = {"sub": str(account_id), "exp": exp, "csrf": "csrf-token", "sv": session_version}
    return jwt.encode(payload, get_settings().secret_key, algorithm="HS256")


def test_decode_session_roundtrip() -> None:
    account_id = uuid.uuid4()
    token = _token(account_id, session_version=3)
    payload = _decode_session(token)
    assert payload is not None
    assert payload["sub"] == str(account_id)
    assert payload["sv"] == 3


def test_decode_session_rejects_garbage() -> None:
    assert _decode_session("not-a-jwt") is None


def test_optional_principal_reads_guest_without_session() -> None:
    principal = optional_principal(
        db=MagicMock(),
        zivo_session=None,
        zivo_demo_id="b" * 32,
        x_zivo_guest_id=None,
    )
    assert principal.account_id is None
    assert principal.guest_id == "b" * 32


def test_optional_principal_checks_session_version() -> None:
    account_id = uuid.uuid4()
    token = _token(account_id, session_version=1)
    db = MagicMock()
    db.execute.return_value.first.return_value = (2,)
    principal = optional_principal(
        db=db,
        zivo_session=token,
        zivo_demo_id=None,
        x_zivo_guest_id=None,
    )
    assert principal.account_id is None


def test_optional_principal_accepts_matching_session_version() -> None:
    account_id = uuid.uuid4()
    token = _token(account_id, session_version=4)
    db = MagicMock()
    db.execute.return_value.first.return_value = (4,)
    principal = optional_principal(
        db=db,
        zivo_session=token,
        zivo_demo_id=None,
        x_zivo_guest_id=None,
    )
    assert principal.account_id == account_id


def test_require_internal_key_accepts_secret() -> None:
    require_internal_key(x_zivo_internal_key=get_settings().secret_key)


def test_require_internal_key_rejects_wrong() -> None:
    with pytest.raises(HTTPException) as exc:
        require_internal_key(x_zivo_internal_key="nope")
    assert exc.value.status_code == 401
