"""Anonymous guest session — one id via httponly cookie + optional client header."""

from __future__ import annotations

import re

from fastapi import Cookie, Depends, Header, HTTPException, Response

from app.engine_runtime import pick
from app.models import Account
from app.services.auth import get_optional_user
from app.services.usage import DEMO_COOKIE, ensure_demo_cookie

GUEST_ID_HEADER = "X-Zivo-Guest-Id"
_GUEST_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def normalize_guest_id(raw: str | None) -> str | None:
    return pick(bool(raw) and bool(_GUEST_ID_RE.fullmatch(raw or "")), lambda: raw, lambda: None)


def _select_guest_id(cookie_id: str | None, header_id: str | None) -> str | None:
    cookie = normalize_guest_id(cookie_id)
    header = normalize_guest_id(header_id)
    return pick(
        bool(cookie and header and cookie != header),
        lambda: cookie,
        lambda: cookie or header,
    )


def publish_guest_id(response: Response, guest_id: str) -> None:
    response.headers[GUEST_ID_HEADER] = guest_id


def resolve_guest_id(
    response: Response,
    cookie_id: str | None,
    header_id: str | None,
) -> str:
    """Return stable guest id; allocate and Set-Cookie when missing."""
    chosen = _select_guest_id(cookie_id, header_id)
    guest_id = ensure_demo_cookie(response, chosen)
    publish_guest_id(response, guest_id)
    return guest_id


def read_guest_id(cookie_id: str | None, header_id: str | None) -> str | None:
    """Read guest id from cookie or header without allocating a new session."""
    return _select_guest_id(cookie_id, header_id)


def read_guest_id_from_cookie(cookie_id: str | None) -> str | None:
    """Read guest id from httponly cookie only (claim / auth flows)."""
    return normalize_guest_id(cookie_id)


def guest_id_for_user(
    user: Account | None,
    response: Response,
    cookie_id: str | None,
    header_id: str | None,
    *,
    create: bool,
) -> str | None:
    return pick(
        user is not None,
        lambda: None,
        lambda: pick(
            create,
            lambda: resolve_guest_id(response, cookie_id, header_id),
            lambda: read_guest_id(cookie_id, header_id),
        ),
    )


def optional_guest_session(
    response: Response,
    user: Account | None = Depends(get_optional_user),
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
    x_zivo_guest_id: str | None = Header(default=None, alias=GUEST_ID_HEADER),
) -> str | None:
    return guest_id_for_user(user, response, zivo_demo_id, x_zivo_guest_id, create=True)


def guest_session_for_read(
    user: Account | None = Depends(get_optional_user),
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
    x_zivo_guest_id: str | None = Header(default=None, alias=GUEST_ID_HEADER),
) -> str | None:
    return pick(user is not None, lambda: None, lambda: read_guest_id(zivo_demo_id, x_zivo_guest_id))


def require_actor(
    user: Account | None = Depends(get_optional_user),
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
    x_zivo_guest_id: str | None = Header(default=None, alias=GUEST_ID_HEADER),
) -> None:
    """Guard for LLM-cost routes: signed-in user OR a real guest session.

    ``require_csrf_or_guest`` admits a completely bare anonymous request (no
    cookie, no header) — fine for low-cost reads, but LLM-triggering endpoints
    must only run for an identifiable actor so spend can be attributed to an
    account or a guest cookie (and quota'd / rate-limited against it).
    """

    def _deny() -> None:
        raise HTTPException(status_code=401, detail="Authentication required")

    pick(user is not None or read_guest_id(zivo_demo_id, x_zivo_guest_id) is not None, lambda: None, _deny)
