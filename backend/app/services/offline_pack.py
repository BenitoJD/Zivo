"""Offline Mode study packs (ADR 0006).

A pack is a signed, expiring snapshot of one artifact's *full* active question
pool, shipped **with** answer keys and pre-baked per-option feedback, plus the
learner's mastery snapshot. The learner downloads it while online, studies with
zero network, and replays grades through the real engines on reconnect
(``POST /api/mcq/grade/batch``).

Layering (CONVENTIONS 2.1): routes stay thin and call these functions; the pure
assembly + signing helpers are DB-free so they unit-test without Postgres. The
DB helpers read raw ``intel.assertion`` rows directly (``payload`` *with* the
answer key) — they deliberately do **not** call ``_sanitize_assertion_payload``,
which keeps guarding every other read path. The engines (adaptive, calibration,
mastery) stay server-side and run on sync; nothing is forked to the client.

Pack build is an ETA job (``offline.build_pack``), mirroring the
``artifact_store`` ``pending -> building -> ready | failed`` lifecycle, because
backfilling option feedback for a large deck is slow (LLM work) and must not
block the request.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Account, Document

logger = logging.getLogger(__name__)

# Pack schema version — bump when the pack envelope shape changes in a way the
# client can't render from the old shape. The client refuses a pack whose
# schema_version it doesn't understand.
PACK_SCHEMA_VERSION = 1


# --------------------------------------------------------------------------- #
# Signing — pure (DB-free), unit-tested.
# --------------------------------------------------------------------------- #
def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    """Deterministic serialization for signing: sorted keys, no whitespace."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_pack(payload: dict[str, Any], *, secret: str | None = None) -> str:
    """HMAC-SHA256 over the canonical pack payload, hex-encoded.

    The signature lets the client reject a tampered pack and lets the server
    reject a forged/synthesized grade-batch claim. Uses ``Settings.secret_key``
    by default (same root as session JWTs).
    """
    key = (secret or get_settings().secret_key).encode("utf-8")
    return hmac.new(key, _canonical_bytes(payload), hashlib.sha256).hexdigest()


def verify_pack(payload: dict[str, Any], signature: str, *, secret: str | None = None) -> bool:
    """Constant-time check that ``signature`` matches ``payload``."""
    expected = sign_pack(payload, secret=secret)
    return hmac.compare_digest(expected, str(signature))


# --------------------------------------------------------------------------- #
# Pure assembly — DB-free, unit-tested.
# --------------------------------------------------------------------------- #
def assemble_pack(
    *,
    document_id: uuid.UUID,
    artifact: dict[str, Any],
    assertions: list[dict[str, Any]],
    mastery: dict[str, Any],
    serve_mode: str = "learn",
    expires_at: datetime,
) -> dict[str, Any]:
    """Build the pack envelope from already-loaded rows.

    ``assertions`` are raw rows (id, title, summary, payload) with the answer
    key still inside ``payload``. ``mastery`` is the learner's progress snapshot
    (answered_ids, current_page, ability, etc.) the client seeds its state from.

    Pure on purpose: a unit test builds the inputs and asserts the envelope
    without touching Postgres.
    """
    deck = [
        {
            "id": str(a["id"]),
            "title": a.get("title"),
            "summary": a.get("summary"),
            "sequence": i,
            "payload": _strip_internal_keys(a["payload"]),
        }
        for i, a in enumerate(assertions)
    ]
    return {
        "schema_version": PACK_SCHEMA_VERSION,
        "document_id": str(document_id),
        "artifact": artifact,
        "serve_mode": serve_mode,
        "deck": deck,
        "mastery": mastery,
        "question_count": len(deck),
        "expires_at": expires_at.astimezone(timezone.utc).isoformat(),
    }


def _strip_internal_keys(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop QA/generation-internal fields the client never renders.

    Keeps everything the offline engine + UI need: question/stem, options,
    correct_index, correct_indices, is_multi, explanation, option_feedback,
    primary_concept(_key), tags, page_number, serve_mode. Removes only
    generation bookkeeping + quality metadata that would bloat the pack.
    """
    if not isinstance(payload, dict):
        return {}
    internal = {
        "quality", "quality_codes", "qa_metadata", "fingerprint", "canonical_uri",
        "draft_source", "candidate_index", "solve_back", "verdict",
    }
    return {k: v for k, v in payload.items() if k not in internal}


# --------------------------------------------------------------------------- #
# DB helpers — raw SQL, bound params (CONVENTIONS 2.2).
# --------------------------------------------------------------------------- #
def _owner_params(account_id: uuid.UUID | None, guest_id: str | None) -> dict[str, Any]:
    return {"account_id": str(account_id) if account_id else None, "guest_id": guest_id}


def create_pack(
    db: Session,
    *,
    document_id: uuid.UUID,
    account_id: uuid.UUID | None,
    guest_id: str | None,
) -> dict[str, Any]:
    """Insert a ``pending`` pack row scoped to the owner; return ``{id, status}``.

    Exactly one of account_id / guest_id must be set (enforced by the table's
    CHECK constraint). TTL comes from ``Settings.offline_pack_ttl_days``.
    """
    if (account_id is None) == (guest_id is None):
        raise ValueError("exactly one of account_id / guest_id must be set")
    expires_at = datetime.now(timezone.utc) + timedelta(days=get_settings().offline_pack_ttl_days)
    row = db.execute(
        text(
            """
            INSERT INTO qb.offline_packs (document_id, account_id, guest_id, status, expires_at)
            VALUES (:document_id, :account_id, :guest_id, 'pending', :expires_at)
            RETURNING id::text, status
            """
        ),
        {
            "document_id": str(document_id),
            "expires_at": expires_at,
            **_owner_params(account_id, guest_id),
        },
    ).first()
    db.commit()
    return {"id": row[0], "status": row[1]}


def _assertion_rows(db: Session, assertion_ids: list[str]) -> list[dict[str, Any]]:
    """Load raw assertion rows (payload WITH answer key), in the given order."""
    if not assertion_ids:
        return []
    rows = db.execute(
        text(
            """
            SELECT id::text, title, summary, payload
            FROM intel.assertion
            WHERE id = ANY(CAST(:ids AS uuid[]))
            """
        ),
        {"ids": assertion_ids},
    ).mappings().all()
    by_id = {
        r["id"]: {
            "id": r["id"],
            "title": r["title"],
            "summary": r["summary"],
            "payload": r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"]),
        }
        for r in rows
    }
    return [by_id[i] for i in assertion_ids if i in by_id]


def _artifact_meta(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    """Minimal artifact metadata the client needs to render the pack source."""
    doc = db.get(Document, document_id)
    if doc is None:
        return {}
    meta = doc.meta or {}
    return {
        "id": str(document_id),
        "filename": getattr(doc, "filename", None),
        "content_type": getattr(doc, "content_type", None),
        "ingest_kind": getattr(doc, "ingest_kind", None),
        "meta": meta,
    }


def build_pack(db: Session, pack_id: uuid.UUID) -> None:
    """Worker entry point: assemble + sign + persist one pack.

    Sets ``building``; backfills missing option feedback per page (so every
    single-answer question has pre-baked coaching); assembles the envelope from
    the full active pool; signs it; stores payload + signature + count; sets
    ``ready``. On any failure, marks ``failed`` with the message and re-raises.
    """
    from app.services.option_feedback import coach_page_assertions
    from app.services.question_pool import (
        edition_assertion_ids,
        get_progress,
        learner_key_for,
        selected_page_list,
        serve_budget_mode,
    )

    pack = db.execute(
        text(
            """
            SELECT document_id, account_id, guest_id
            FROM qb.offline_packs WHERE id = :id
            """
        ),
        {"id": str(pack_id)},
    ).first()
    if pack is None:
        logger.warning("offline pack %s not found", pack_id)
        return
    document_id = uuid.UUID(str(pack[0]))
    account_id = uuid.UUID(str(pack[1])) if pack[1] else None
    guest_id = pack[2]

    _set_status(db, pack_id, "building")
    try:
        doc = db.get(Document, document_id)
        if doc is None:
            raise RuntimeError("artifact not found")

        # Resolve the learner so answered_ids / mastery reflect THIS learner.
        user = db.get(Account, account_id) if account_id else None
        lk = learner_key_for(user, guest_id)
        serve_mode = serve_budget_mode(doc, learner_key=lk)

        # 1. Backfill per-option feedback for any page that still lacks it, so
        #    the offline grade path has coaching for every single-answer item.
        #    Idempotent (coach_page_assertions only touches assertions without
        #    option_feedback). Multi-select items keep the live grade path and
        #    are simply not pre-baked.
        pages = selected_page_list(doc)
        total_pages = max(len(pages), 1)
        for i, page in enumerate(pages, 1):
            try:
                coach_page_assertions(db, document_id=document_id, page_number=page)
            except Exception:
                logger.debug("coach page %s failed for pack %s", page, pack_id, exc_info=True)
            _set_status(db, pack_id, "building", progress=round(20 * i / total_pages, 2))

        # 2. Re-read the deck after coaching so payloads include fresh feedback.
        assertion_ids = edition_assertion_ids(db, document_id, doc, serve_mode=serve_mode)
        assertions = _assertion_rows(db, assertion_ids)
        _set_status(db, pack_id, "building", progress=70)

        # 3. Mastery snapshot the client seeds its local state from.
        progress = get_progress(doc, learner_key=lk) if lk else {}
        mastery = {
            "answered_ids": [str(x) for x in progress.get("answered_ids") or []],
            "current_page": int(progress.get("current_page") or 1),
            "budget_serve_mode": str(progress.get("budget_serve_mode") or serve_mode),
        }

        expires_at = datetime.now(timezone.utc) + timedelta(days=get_settings().offline_pack_ttl_days)
        pack_payload = assemble_pack(
            document_id=document_id,
            artifact=_artifact_meta(db, document_id),
            assertions=assertions,
            mastery=mastery,
            serve_mode=str(serve_mode),
            expires_at=expires_at,
        )
        signature = sign_pack(pack_payload)

        db.execute(
            text(
                """
                UPDATE qb.offline_packs
                SET pack_payload = CAST(:payload AS jsonb),
                    signature = :signature,
                    question_count = :count,
                    status = 'ready',
                    progress = 100,
                    expires_at = :expires_at,
                    error = NULL,
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": str(pack_id),
                "payload": json.dumps(pack_payload),
                "signature": signature,
                "count": len(assertions),
                "expires_at": expires_at,
            },
        )
        db.commit()
        logger.info("offline pack %s ready (%d questions)", pack_id, len(assertions))
    except Exception as exc:
        logger.exception("offline pack %s build failed", pack_id)
        _set_status(db, pack_id, "failed", error=str(exc)[:500])
        db.commit()
        raise


def _set_status(
    db: Session,
    pack_id: uuid.UUID,
    status: str,
    *,
    progress: float | None = None,
    error: str | None = None,
) -> None:
    clauses = ["status = :status", "updated_at = now()"]
    params: dict[str, Any] = {"id": str(pack_id), "status": status}
    if progress is not None:
        clauses.append("progress = :progress")
        params["progress"] = float(progress)
    if error is not None:
        clauses.append("error = :error")
        params["error"] = error
    db.execute(
        text(f"UPDATE qb.offline_packs SET {', '.join(clauses)} WHERE id = :id"),
        params,
    )
    db.commit()


def get_pack(
    db: Session,
    pack_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None,
    guest_id: str | None,
    include_payload: bool = False,
) -> dict[str, Any] | None:
    """Ownership-checked read. Payload is opt-in (download path only)."""
    cols = [
        "id::text", "document_id::text", "status", "progress",
        "question_count", "expires_at", "created_at", "updated_at", "error",
    ]
    if include_payload:
        cols += ["pack_payload", "signature"]
    owner_clause, owner_params = _owner_filter(account_id, guest_id)
    row = db.execute(
        text(
            f"""
            SELECT {', '.join(cols)} FROM qb.offline_packs
            WHERE id = :id AND {owner_clause}
            """
        ),
        {"id": str(pack_id), **owner_params},
    ).mappings().first()
    if row is None:
        return None
    return dict(row)


def list_packs(
    db: Session,
    *,
    document_id: uuid.UUID | None = None,
    account_id: uuid.UUID | None,
    guest_id: str | None,
) -> list[dict[str, Any]]:
    """A learner's packs (optionally for one source) — metadata only, no payload."""
    where = ["status IS NOT NULL"]
    params: dict[str, Any] = {}
    if document_id is not None:
        where.append("document_id = :document_id")
        params["document_id"] = str(document_id)
    owner_clause, owner_params = _owner_filter(account_id, guest_id)
    where.append(owner_clause)
    params.update(owner_params)
    rows = db.execute(
        text(
            f"""
            SELECT id::text, document_id::text, status, progress, question_count,
                   expires_at, created_at
            FROM qb.offline_packs
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            """
        ),
        params,
    ).mappings().all()
    return [dict(r) for r in rows]


def revoke_pack(
    db: Session,
    pack_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None,
    guest_id: str | None,
) -> bool:
    """Delete a pack row (owner-scoped). Returns whether a row was removed."""
    owner_clause, owner_params = _owner_filter(account_id, guest_id)
    result = db.execute(
        text(f"DELETE FROM qb.offline_packs WHERE id = :id AND {owner_clause}"),
        {"id": str(pack_id), **owner_params},
    )
    db.commit()
    return result.rowcount > 0


def _owner_filter(
    account_id: uuid.UUID | None, guest_id: str | None
) -> tuple[str, dict[str, Any]]:
    if account_id is not None:
        return "account_id = :account_id", {"account_id": str(account_id)}
    if guest_id is not None:
        return "guest_id = :guest_id", {"guest_id": guest_id}
    # No owner → match nothing (safe default; never returns others' packs).
    return "FALSE", {}


def is_expired(row: dict[str, Any]) -> bool:
    """True if the pack row is past its ``expires_at``."""
    exp = row.get("expires_at")
    if exp is None:
        return True
    if isinstance(exp, str):
        exp = datetime.fromisoformat(exp.replace("Z", "+00:00"))
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > exp
