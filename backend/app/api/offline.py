"""Offline Mode — prepare, poll, download, and revoke study packs (ADR 0006).

Thin route layer: validate, resolve access, delegate to ``offline_pack`` service,
shape the response. Pack creation enqueues the ``offline.build_pack`` ETA job
(off the request path, since feedback backfill is slow). The download endpoint
is the single place that ships answer keys to the device.
"""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.access import require_document
from app.config import get_settings
from app.db import get_db
from app.models import Account
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.guest_session import guest_session_for_read
from app.services.jobs import enqueue_offline_pack
from app.services.offline_pack import (
    create_pack,
    get_pack,
    is_expired,
    list_packs,
    revoke_pack,
    verify_pack,
)
from app.services.rate_limit import rate_limit_dependency

logger = logging.getLogger(__name__)
router = APIRouter()


class PackCreateIn(BaseModel):
    document_id: uuid.UUID


def _require_offline_enabled() -> None:
    if not get_settings().offline_mode_enabled:
        raise HTTPException(status_code=404, detail="Not found")


def _ensure_actor(user: Account | None, guest_id: str | None) -> None:
    if not user and not guest_id:
        raise HTTPException(status_code=401, detail="Authentication required")


@router.post(
    "/packs",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
def create_offline_pack(
    body: PackCreateIn,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Create a ``pending`` pack for an artifact and enqueue its build."""
    _require_offline_enabled()
    _ensure_actor(user, guest_id)
    require_document(db, body.document_id, user, guest_id)  # access check + 404
    account_id = getattr(user, "id", None) if user else None
    pack = create_pack(db, document_id=body.document_id, account_id=account_id, guest_id=guest_id)
    enqueue_offline_pack(db, uuid.UUID(pack["id"]))
    return {"id": pack["id"], "status": pack["status"]}


@router.get(
    "/packs/{pack_id}",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
def get_offline_pack(
    pack_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Poll target — status, progress, metadata (no payload)."""
    _require_offline_enabled()
    _ensure_actor(user, guest_id)
    account_id = getattr(user, "id", None) if user else None
    pack = get_pack(db, pack_id, account_id=account_id, guest_id=guest_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Not found")
    return pack


@router.get(
    "/packs/{pack_id}/download",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
def download_offline_pack(
    pack_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Return the full signed pack payload — the only endpoint that ships keys."""
    _require_offline_enabled()
    _ensure_actor(user, guest_id)
    account_id = getattr(user, "id", None) if user else None
    pack = get_pack(
        db, pack_id, account_id=account_id, guest_id=guest_id, include_payload=True
    )
    if pack is None:
        raise HTTPException(status_code=404, detail="Not found")
    if pack.get("status") != "ready":
        raise HTTPException(status_code=409, detail=f"Pack is {pack.get('status')}")
    if is_expired(pack):
        raise HTTPException(status_code=410, detail="Pack expired")
    payload = pack.get("pack_payload")
    if not isinstance(payload, dict):
        payload = json.loads(payload) if isinstance(payload, str) else {}
    signature = str(pack.get("signature") or "")
    if not signature or not verify_pack(payload, signature):
        logger.error("offline pack %s signature verification failed", pack_id)
        raise HTTPException(status_code=500, detail="Pack integrity check failed")
    return payload


@router.get(
    "/packs",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
def list_offline_packs(
    document_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """A learner's packs, optionally scoped to one source ("downloaded?" badge)."""
    _require_offline_enabled()
    _ensure_actor(user, guest_id)
    account_id = getattr(user, "id", None) if user else None
    return {"packs": list_packs(db, document_id=document_id, account_id=account_id, guest_id=guest_id)}


@router.delete(
    "/packs/{pack_id}",
    dependencies=[Depends(require_csrf_or_guest), Depends(rate_limit_dependency)],
)
def delete_offline_pack(
    pack_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: Account | None = Depends(get_optional_user),
    guest_id: str | None = Depends(guest_session_for_read),
) -> dict:
    """Revoke a pack server-side (the client also drops its local copy)."""
    _require_offline_enabled()
    _ensure_actor(user, guest_id)
    account_id = getattr(user, "id", None) if user else None
    removed = revoke_pack(db, pack_id, account_id=account_id, guest_id=guest_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}
