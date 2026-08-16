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

from app.services.document_access import require_document
from app.config import get_settings
from app.db import get_db
from app.engine_runtime import Pred, Rule, apply, first_match, pick
from app.models import Account
from app.services.auth import get_optional_user, require_csrf_or_guest
from app.services.auth_gate import evaluate_auth_gate
from app.services.guest_session import guest_session_for_read
from app.services.http_outcome import evaluate_http_outcome
from app.services.jobs import enqueue_offline_pack
from app.services.offline_pack import (
    create_pack,
    get_pack,
    is_expired,
    list_packs,
    revoke_pack,
    verify_pack,
)
from app.services.presence import evaluate_presence
from app.services.rate_limit import rate_limit_dependency

logger = logging.getLogger(__name__)
router = APIRouter()


class PackCreateIn(BaseModel):
    document_id: uuid.UUID


def _raise(exc: BaseException) -> None:
    raise exc


def _http(action: str, detail: str) -> None:
    _raise(HTTPException(status_code=evaluate_http_outcome(action).status, detail=detail))


def _account_id(user: Account | None):
    return pick(bool(user), lambda: user.id, lambda: None)


def _require_offline_enabled() -> None:
    pick(
        not get_settings().offline_mode_enabled,
        lambda: _http("missing", "Not found"),
        lambda: None,
    )


def _ensure_actor(user: Account | None, guest_id: str | None) -> None:
    apply(
        evaluate_auth_gate(account=user, allow_guest=bool(guest_id)).action,
        {
            "allow": lambda: None,
            "guest": lambda: None,
            "redirect": lambda: _http("redirect", "Authentication required"),
            "deny": lambda: _http("deny", "Authentication required"),
        },
    )


def _require_pack(pack) -> dict:
    apply(
        evaluate_presence(pack).action,
        {
            "missing": lambda: _http("missing", "Not found"),
            "empty": lambda: _http("missing", "Not found"),
            "ok": lambda: None,
        },
    )
    return pack


def _pack_payload(pack: dict):
    payload = pack.get("pack_payload")
    return pick(
        isinstance(payload, dict),
        lambda: payload,
        lambda: pick(isinstance(payload, str), lambda: json.loads(payload), lambda: {}),
    )


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
    require_document(db, body.document_id, user, guest_id)
    pack = create_pack(
        db, document_id=body.document_id, account_id=_account_id(user), guest_id=guest_id
    )
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
    pack = get_pack(db, pack_id, account_id=_account_id(user), guest_id=guest_id)
    return _require_pack(pack)


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
    pack = get_pack(
        db, pack_id, account_id=_account_id(user), guest_id=guest_id, include_payload=True
    )
    _require_pack(pack)
    apply(
        first_match(
            (
                Rule(when=(Pred("not_ready", "truthy"),), action="conflict"),
                Rule(when=(Pred("expired", "truthy"),), action="expired"),
                Rule(when=(), action="ok"),
            ),
            {
                "not_ready": pack.get("status") != "ready",
                "expired": is_expired(pack),
            },
        ).action,
        {
            "conflict": lambda: _http("conflict", f"Pack is {pack.get('status')}"),
            "expired": lambda: _raise(HTTPException(status_code=410, detail="Pack expired")),
            "ok": lambda: None,
        },
    )
    payload = _pack_payload(pack)
    signature = str(pack.get("signature") or "")
    pick(
        not signature or not verify_pack(payload, signature),
        lambda: (
            logger.error("offline pack %s signature verification failed", pack_id),
            _raise(HTTPException(status_code=500, detail="Pack integrity check failed")),
        ),
        lambda: None,
    )
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
    return {
        "packs": list_packs(
            db, document_id=document_id, account_id=_account_id(user), guest_id=guest_id
        )
    }


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
    removed = revoke_pack(db, pack_id, account_id=_account_id(user), guest_id=guest_id)
    pick(not removed, lambda: _http("missing", "Not found"), lambda: None)
    return {"ok": True}
