"""DB-backed generation cache — shared across workers, survives restarts.

Replaces the in-process _BatchDraftCache and _page_context_cache so that:
  - the 4 CPU workers share one cache instead of 4 private copies,
  - a worker crash no longer forces the demo doc (or a re-queried page) to
    re-pay the LLM generation cost.

Two `kind` values are stored:
  - 'batch_drafts'   — the raw MCQ draft list from generate_quality_mcq_batch
  - 'page_context'   — stable truncated page text used as the cached LLM prefix

Entries carry a created_at; TTL is enforced on read and swept opportunistically
on write. Default TTL (24h) is conservative — drafts are cheap to regenerate and
the quality gate still runs on every retrieval, so a stale hit only risks
slightly less-fresh LLM output.
"""

from __future__ import annotations
import logging

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import pick

DEFAULT_TTL_SECONDS = 24 * 60 * 60
# Sweep at most this many expired rows per write to bound write latency.
SWEEP_BATCH = 50


logger = logging.getLogger(__name__)

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expired_expr(ttl_seconds: int) -> str:
    cutoff = _now() - timedelta(seconds=ttl_seconds)
    return cutoff.isoformat()


def get(
    db: Session,
    *,
    kind: str,
    cache_key: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> Any | None:
    row = db.execute(
        text(
            """
            SELECT value FROM qb.generation_cache
            WHERE cache_key = :key AND kind = :kind
              AND created_at >= :cutoff
            """
        ),
        {"key": cache_key, "kind": kind, "cutoff": _expired_expr(ttl_seconds)},
    ).first()
    return pick(not row, lambda: None, lambda: row[0])


def put(
    db: Session,
    *,
    kind: str,
    cache_key: str,
    value: Any,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> None:
    db.execute(
        text(
            """
            INSERT INTO qb.generation_cache (cache_key, kind, value, created_at)
            VALUES (:key, :kind, CAST(:value AS jsonb), now())
            ON CONFLICT (cache_key) DO UPDATE SET
              kind = EXCLUDED.kind,
              value = EXCLUDED.value,
              created_at = now()
            """
        ),
        {
            "key": cache_key,
            "kind": kind,
            "value": json.dumps(value, default=str),
        },
    )
    _opportunistic_sweep(db, ttl_seconds, kind)


def _opportunistic_sweep(db: Session, ttl_seconds: int, kind: str) -> None:
    """Delete a bounded batch of expired rows. Best-effort; ignore errors.

    Same-kind only: kinds carry different TTLs (vision_ocr keeps entries for
    30 days), so a 24h-TTL writer must never sweep another kind's live rows.
    """
    try:
        db.execute(
            text(
                """
                DELETE FROM qb.generation_cache
                WHERE ctid IN (
                    SELECT ctid FROM qb.generation_cache
                    WHERE kind = :kind AND created_at < :cutoff
                    LIMIT :batch
                )
                """
            ),
            {"kind": kind, "cutoff": _expired_expr(ttl_seconds), "batch": SWEEP_BATCH},
        )
    except Exception:
        logger.debug("generation cache sweep failed", exc_info=True)


# --- key builders (mirror the in-process cache hashing) ---------------------


def batch_drafts_key(
    page_number: int,
    targets: list[dict[str, Any]],
    prior_mcqs: list[dict[str, Any]] | None,
    *,
    model_id: uuid.UUID | None = None,
    content_type: str | None = None,
    prompt_version: str = "v1",
    page_text: str = "",
) -> str:
    """Deterministic key for a batch-draft cache entry.

    The page TEXT digest is the identity of what drafts were generated from —
    without it, two documents whose triage produced the same positional aspect
    slugs on the same page number collide and serve each other's drafts for the
    TTL. Sorted aspect keys define the intent; the prior MCQ digest is included
    so two batches with different "don't repeat these" histories don't collide.
    Model / content_type / prompt_version bind the entry so a model swap or
    prompt bump cannot serve stale drafts.
    """
    import hashlib

    aspect_keys = sorted(str(t.get("key") or "") for t in targets)
    h = hashlib.sha256()
    h.update(str(page_number).encode("ascii"))
    h.update(b"\x1f")
    h.update(hashlib.sha256((page_text or "").encode("utf-8", "ignore")).hexdigest().encode("ascii"))
    h.update(b"\x1f")
    h.update("\x1e".join(aspect_keys).encode("utf-8", "ignore"))
    h.update(b"\x1f")
    h.update(str(model_id or "").encode("ascii"))
    h.update(b"\x1f")
    h.update((content_type or "").encode("utf-8", "ignore"))
    h.update(b"\x1f")
    h.update((prompt_version or "v1").encode("ascii"))

    def _with_prior() -> str:
        prior_digest = hashlib.sha256(
            json.dumps(prior_mcqs, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        h.update(b"\x1f")
        h.update(prior_digest.encode("ascii"))
        return h.hexdigest()

    return pick(bool(prior_mcqs), _with_prior, h.hexdigest)


def page_context_key(document_id: uuid.UUID, page_number: int) -> str:
    return f"ctx:{document_id}:{page_number}"


def purge_for_document(db: Session, document_id: uuid.UUID) -> int:
    """Drop generation_cache rows keyed to a document (page_context).

    Content-hash keys (chunk_map, verify, triage, …) expire via TTL — they are
    not document-scoped and may still help other docs with identical text.
    """
    result = db.execute(
        text(
            """
            DELETE FROM qb.generation_cache
            WHERE kind = 'page_context'
              AND cache_key LIKE :prefix
            """
        ),
        {"prefix": f"ctx:{document_id}:%"},
    )
    return int(result.rowcount or 0)
