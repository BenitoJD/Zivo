"""Semantic cache for LLM chat responses (GPTCache pattern).

Repeated or semantically-similar questions ("summarize", "key points", common
demo-doc queries) skip the LLM call entirely. We embed the query, look up the
nearest cached response by cosine similarity, and return it above a threshold.
On a miss we store the freshly-generated response.

Cosine search is index-backed (HNSW on llm_response_cache.embedding, migration
007). scope_hash folds page/selection scope into the key so scoped answers
aren't served for unscoped questions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Cosine similarity threshold above which a cached answer is reused. 0.92 is
# conservative — near-paraphrase. Lower to 0.88 for more hits at the cost of
# occasional off-target answers.
SIMILARITY_THRESHOLD = 0.92


def _scope_hash(artifact_id: uuid.UUID, scope: dict) -> str:
    """Stable hash of (artifact, scope) so scoping is part of the cache key."""
    key = {
        "artifact_id": str(artifact_id),
        "page_start": scope.get("page_start"),
        "page_end": scope.get("page_end"),
        "has_selection": bool(scope.get("selection_text")),
        "current_page": scope.get("current_page"),
        "question_number": scope.get("question_number"),
        "current_assertion_id": scope.get("current_assertion_id"),
        "confirmed_choice_index": scope.get("confirmed_choice_index"),
        "answer_correct": scope.get("answer_correct"),
        "mentions": sorted(scope.get("mentions") or []),
    }
    raw = json.dumps(key, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def get_cached_response(
    db: Session,
    *,
    document_id: uuid.UUID,
    artifact_captured_at: datetime,
    scope: dict,
    query_embedding: list[float],
    threshold: float = SIMILARITY_THRESHOLD,
) -> dict | None:
    """Return {response_text, citations} if a similar query is cached, else None."""
    vec_literal = "[" + ",".join(str(x) for x in query_embedding) + "]"
    row = db.execute(
        text(
            """
            SELECT id, response_text, citations,
                   1 - (embedding <=> CAST(:vec AS vector)) AS similarity
            FROM llm_response_cache
            WHERE artifact_id = CAST(:artifact_id AS uuid)
              AND artifact_captured_at = :artifact_captured_at
              AND scope_hash = :scope_hash
              AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:vec AS vector)
            LIMIT 1
            """
        ),
        {
            "vec": vec_literal,
            "artifact_id": str(document_id),
            "artifact_captured_at": artifact_captured_at,
            "scope_hash": _scope_hash(document_id, scope),
        },
    ).mappings().first()
    if not row:
        return None
    similarity = float(row["similarity"])
    if similarity < threshold:
        return None
    return {
        "response_text": row["response_text"],
        "citations": row["citations"],
        "similarity": similarity,
    }


def store_response(
    db: Session,
    *,
    document_id: uuid.UUID,
    artifact_captured_at: datetime,
    scope: dict,
    query_embedding: list[float],
    response_text: str,
    citations: list[dict],
) -> None:
    """Persist a freshly-generated response for future cache hits."""
    vec_literal = "[" + ",".join(str(x) for x in query_embedding) + "]"
    try:
        db.execute(
            text(
                """
                INSERT INTO llm_response_cache
                    (id, artifact_id, artifact_captured_at, scope_hash, embedding, response_text, citations)
                VALUES
                    (:id, CAST(:artifact_id AS uuid), :artifact_captured_at, :scope_hash,
                     CAST(:embedding AS vector), :response_text, CAST(:citations AS jsonb))
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "artifact_id": str(document_id),
                "artifact_captured_at": artifact_captured_at,
                "scope_hash": _scope_hash(document_id, scope),
                "embedding": vec_literal,
                "response_text": response_text,
                "citations": json.dumps({"sources": citations}),
            },
        )
        db.commit()
    except Exception:
        logger.exception("failed to store cached response")
        db.rollback()


def cleanup_stale_cache(db: Session, max_age_days: int = 30) -> int:
    """Delete cache rows older than ``max_age_days``. Returns rows removed."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    result = db.execute(
        text("DELETE FROM llm_response_cache WHERE created_at < :cutoff"),
        {"cutoff": cutoff},
    )
    db.commit()
    return int(result.rowcount or 0)
