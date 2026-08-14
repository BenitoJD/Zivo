"""Anonymous guest session mint. Identity login lives on the auth service."""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Response

from app.services.guest_session import publish_guest_id, read_guest_id_from_cookie
from app.services.usage import DEMO_COOKIE, ensure_demo_cookie

router = APIRouter()


@router.post("/guest")
def mint_guest_session(
    response: Response,
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
) -> dict[str, str]:
    """Mint or refresh the anonymous guest cookie without listing sources."""
    guest_id = ensure_demo_cookie(response, read_guest_id_from_cookie(zivo_demo_id))
    publish_guest_id(response, guest_id)
    return {"guest_id": guest_id}
