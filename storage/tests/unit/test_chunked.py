"""Chunked upload session access and part validation."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.services.chunked import _assert_session_access, upload_part
from app.services.guest import Principal


def _row(**overrides: object) -> dict:
    base = {
        "id": uuid.uuid4(),
        "account_id": uuid.uuid4(),
        "guest_id": None,
        "filename": "notes.pdf",
        "content_type": "application/pdf",
        "total_size": 10,
        "chunk_size": 5,
        "storage_key": "users/x/a/notes.pdf",
        "multipart_upload_id": "up-1",
        "parts": {},
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    base.update(overrides)
    return base


def test_session_access_requires_matching_account() -> None:
    owner = uuid.uuid4()
    row = _row(account_id=owner)
    other = Principal(account_id=uuid.uuid4(), guest_id=None)
    with pytest.raises(HTTPException) as exc:
        _assert_session_access(row, other)
    assert exc.value.status_code == 404


def test_session_access_allows_owner() -> None:
    owner = uuid.uuid4()
    row = _row(account_id=owner)
    got = _assert_session_access(row, Principal(account_id=owner, guest_id=None))
    assert got["filename"] == "notes.pdf"


def test_session_access_allows_guest() -> None:
    row = _row(account_id=None, guest_id="a" * 32)
    got = _assert_session_access(row, Principal(account_id=None, guest_id="a" * 32))
    assert got["guest_id"] == "a" * 32


def test_expired_session_is_gone() -> None:
    row = _row(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    with pytest.raises(HTTPException) as exc:
        _assert_session_access(row, Principal(account_id=row["account_id"], guest_id=None))
    assert exc.value.status_code == 410


def test_upload_part_rejects_bad_number(monkeypatch: pytest.MonkeyPatch) -> None:
    owner = uuid.uuid4()
    row = _row(account_id=owner, total_size=10, chunk_size=5)
    db = MagicMock()
    monkeypatch.setattr("app.services.chunked._session_row", lambda *_a, **_k: row)
    with pytest.raises(HTTPException) as exc:
        upload_part(db, row["id"], 0, b"xxxxx", principal=Principal(account_id=owner, guest_id=None))
    assert exc.value.status_code == 400
