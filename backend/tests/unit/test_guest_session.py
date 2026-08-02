"""Guest session resolution — cookie + header fallback."""

from __future__ import annotations

import pytest
from fastapi import HTTPException, Response

from app.services.guest_session import (
    GUEST_ID_HEADER,
    normalize_guest_id,
    publish_guest_id,
    read_guest_id,
    read_guest_id_from_cookie,
    require_actor,
    resolve_guest_id,
)


def test_normalize_guest_id() -> None:
    valid = "a" * 32
    assert normalize_guest_id(valid) == valid
    assert normalize_guest_id("not-uuid") is None
    assert normalize_guest_id(None) is None


def test_resolve_allocates_when_missing() -> None:
    response = Response()
    guest_id = resolve_guest_id(response, None, None)
    assert len(guest_id) == 32
    assert response.headers[GUEST_ID_HEADER] == guest_id


def test_resolve_prefers_cookie_over_conflicting_header() -> None:
    response = Response()
    cookie = "b" * 32
    header = "c" * 32
    guest_id = resolve_guest_id(response, cookie, header)
    assert guest_id == cookie


def test_read_uses_header_when_cookie_missing() -> None:
    header = "d" * 32
    assert read_guest_id(None, header) == header


def test_read_guest_id_from_cookie_only() -> None:
    cookie = "f" * 32
    assert read_guest_id_from_cookie(cookie) == cookie
    assert read_guest_id_from_cookie(None) is None
    assert read_guest_id_from_cookie("not-valid") is None


def test_publish_guest_id() -> None:
    response = Response()
    gid = "e" * 32
    publish_guest_id(response, gid)
    assert response.headers[GUEST_ID_HEADER] == gid


def test_require_actor_accepts_signed_in_user() -> None:
    class FakeUser:
        id = "00000000-0000-4000-8000-000000000001"

    require_actor(user=FakeUser(), zivo_demo_id=None, x_zivo_guest_id=None)


def test_require_actor_accepts_guest_cookie() -> None:
    require_actor(user=None, zivo_demo_id="b" * 32, x_zivo_guest_id=None)


def test_require_actor_accepts_guest_header() -> None:
    require_actor(user=None, zivo_demo_id=None, x_zivo_guest_id="c" * 32)


def test_require_actor_rejects_bare_anonymous() -> None:
    with pytest.raises(HTTPException) as exc_info:
        require_actor(user=None, zivo_demo_id=None, x_zivo_guest_id=None)
    assert exc_info.value.status_code == 401
