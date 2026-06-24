"""Guest session resolution — cookie + header fallback."""

from __future__ import annotations

from fastapi import Response

from app.services.guest_session import (
    GUEST_ID_HEADER,
    normalize_guest_id,
    publish_guest_id,
    read_guest_id,
    read_guest_id_from_cookie,
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
