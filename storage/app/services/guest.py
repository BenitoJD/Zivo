"""Guest cookie / header verification. Product mints guests; storage only reads."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException

from app.engine_runtime import pick

DEMO_COOKIE = "zivo_demo_id"
GUEST_ID_HEADER = "X-Zivo-Guest-Id"
_GUEST_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def _raise(exc: BaseException) -> None:
    raise exc


def normalize_guest_id(raw: str | None) -> str | None:
    return pick(bool(raw) and bool(_GUEST_ID_RE.fullmatch(raw or "")), lambda: raw, lambda: None)


def read_guest_id(cookie_id: str | None, header_id: str | None) -> str | None:
    cookie = normalize_guest_id(cookie_id)
    header = normalize_guest_id(header_id)
    return pick(
        bool(cookie) and bool(header) and cookie != header,
        lambda: cookie,
        lambda: cookie or header,
    )


@dataclass(frozen=True)
class Principal:
    account_id: UUID | None
    guest_id: str | None

    def require_identity(self) -> None:
        pick(
            self.account_id is None and self.guest_id is None,
            lambda: _raise(HTTPException(status_code=401, detail="Not authenticated")),
            lambda: None,
        )
