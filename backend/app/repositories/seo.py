"""Raw SQL for qb.seo_* tables."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine_runtime import pick
from app.services.chunks import pgvector_literal
from app.services.seo_gate import (
    RETRYABLE_EDITION_DIGEST_REASONS,
    plan_seo_candidate_query_limit,
    seo_candidate_min_chars,
)


def _maybe_iso(value: Any) -> str | None:
    return pick(value is not None, lambda: value.isoformat(), lambda: None)


def _row_dict(row: Any) -> dict[str, Any] | None:
    return pick(bool(row), lambda: dict(row), lambda: None)


def _count(row: Any) -> int:
    return pick(bool(row), lambda: int(row[0]), lambda: 0)


def get_settings(db: Session) -> dict[str, Any]:
    row = db.execute(
        text(
            """
            SELECT cook_enabled, soft_max_per_day, updated_at
            FROM qb.seo_settings WHERE id = 1
            """
        )
    ).mappings().first()
    return pick(
        not row,
        lambda: {"cook_enabled": False, "soft_max_per_day": 20, "updated_at": None},
        lambda: dict(row),
    )


def update_settings(
    db: Session,
    *,
    cook_enabled: bool | None = None,
    soft_max_per_day: int | None = None,
) -> dict[str, Any]:
    cur = get_settings(db)
    enabled = pick(cook_enabled is None, lambda: cur["cook_enabled"], lambda: bool(cook_enabled))
    soft = pick(soft_max_per_day is None, lambda: cur["soft_max_per_day"], lambda: int(soft_max_per_day))
    soft = max(1, min(soft, 100))
    db.execute(
        text(
            """
            INSERT INTO qb.seo_settings (id, cook_enabled, soft_max_per_day, updated_at)
            VALUES (1, :en, :soft, now())
            ON CONFLICT (id) DO UPDATE SET
              cook_enabled = EXCLUDED.cook_enabled,
              soft_max_per_day = EXCLUDED.soft_max_per_day,
              updated_at = now()
            """
        ),
        {"en": enabled, "soft": soft},
    )
    db.commit()
    return get_settings(db)


def count_published_on_day(db: Session, day: date) -> int:
    row = db.execute(
        text(
            """
            SELECT COUNT(*)::int AS n
            FROM qb.seo_post
            WHERE status = 'published'
              AND (published_at AT TIME ZONE 'Asia/Kolkata')::date = :day
            """
        ),
        {"day": day},
    ).first()
    return _count(row)


def count_sd_published_on_day(db: Session, day: date) -> int:
    row = db.execute(
        text(
            """
            SELECT COUNT(*)::int AS n
            FROM qb.seo_post
            WHERE status = 'published'
              AND stream = 'system_design'
              AND (published_at AT TIME ZONE 'Asia/Kolkata')::date = :day
            """
        ),
        {"day": day},
    ).first()
    return _count(row)


def attempt_exists(db: Session, source_kind: str, source_key: str) -> bool:
    row = db.execute(
        text(
            """
            SELECT 1 FROM qb.seo_cook_attempt
            WHERE source_kind = :k AND source_key = :s
            LIMIT 1
            """
        ),
        {"k": source_kind, "s": source_key},
    ).first()
    return row is not None


def latest_attempt(
    db: Session, source_kind: str, source_key: str
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT outcome, reason
            FROM qb.seo_cook_attempt
            WHERE source_kind = :k AND source_key = :s
            LIMIT 1
            """
        ),
        {"k": source_kind, "s": source_key},
    ).mappings().first()
    return _row_dict(row)


def edition_digest_may_enqueue(
    db: Session, source_kind: str, source_key: str
) -> bool:
    """True when no cook ran yet, or the last attempt failed transiently."""
    attempt = latest_attempt(db, source_kind, source_key)
    return pick(
        not attempt,
        lambda: True,
        lambda: pick(
            attempt.get("outcome") == "published",
            lambda: False,
            lambda: str(attempt.get("reason") or "") in RETRYABLE_EDITION_DIGEST_REASONS,
        ),
    )


def record_attempt(
    db: Session,
    *,
    source_kind: str,
    source_key: str,
    outcome: str,
    reason: str = "",
    post_id: uuid.UUID | None = None,
) -> None:
    db.execute(
        text(
            """
            INSERT INTO qb.seo_cook_attempt (source_kind, source_key, outcome, reason, post_id)
            VALUES (:k, :s, :o, :r, :p)
            ON CONFLICT (source_kind, source_key) DO UPDATE SET
              outcome = EXCLUDED.outcome,
              reason = EXCLUDED.reason,
              post_id = COALESCE(EXCLUDED.post_id, qb.seo_cook_attempt.post_id)
            """
        ),
        {
            "k": source_kind,
            "s": source_key,
            "o": outcome,
            "r": (reason or "")[:500],
            "p": post_id,
        },
    )


def next_author_name(db: Session) -> str:
    row = db.execute(
        text(
            """
            SELECT a.name
            FROM qb.seo_author a
            WHERE a.active = true
            ORDER BY (
              SELECT COUNT(*) FROM qb.seo_post p
              WHERE p.author_name = a.name AND p.status = 'published'
            ) ASC,
            a.sort_order ASC
            LIMIT 1
            """
        )
    ).first()
    return pick(bool(row), lambda: str(row[0]), lambda: "Benito JD")


def fingerprint_taken(db: Session, fingerprint: str) -> bool:
    row = db.execute(
        text(
            """
            SELECT 1 FROM qb.seo_post
            WHERE topic_fingerprint = :fp AND status = 'published'
            LIMIT 1
            """
        ),
        {"fp": fingerprint},
    ).first()
    return row is not None


def get_published_post_by_fingerprint(
    db: Session, fingerprint: str
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT id, slug, status, topic_fingerprint
            FROM qb.seo_post
            WHERE topic_fingerprint = :fp AND status = 'published'
            ORDER BY published_at DESC NULLS LAST, created_at DESC
            LIMIT 1
            """
        ),
        {"fp": fingerprint},
    ).mappings().first()
    return _row_dict(row)


def slug_taken(db: Session, slug: str) -> bool:
    row = db.execute(
        text("SELECT 1 FROM qb.seo_post WHERE slug = :s LIMIT 1"),
        {"s": slug},
    ).first()
    return row is not None


def _max_cosine(db: Session, embedding: list[float]) -> float:
    vec = pgvector_literal(embedding)
    row = db.execute(
        text(
            """
            SELECT COALESCE(
              MAX(1 - (embedding <=> CAST(:vec AS vector))),
              0.0
            ) AS sim
            FROM qb.seo_post
            WHERE status = 'published' AND embedding IS NOT NULL
            """
        ),
        {"vec": vec},
    ).first()
    return pick(
        bool(row) and row[0] is not None,
        lambda: float(row[0]),
        lambda: 0.0,
    )


def max_published_cosine(db: Session, embedding: list[float]) -> float:
    return pick(
        not embedding,
        lambda: 0.0,
        lambda: _max_cosine(db, embedding),
    )


def insert_post(
    db: Session,
    *,
    slug: str,
    title: str,
    lede: str,
    body_md: str,
    format: str,
    stream: str,
    author_name: str,
    topic_fingerprint: str,
    embedding: list[float] | None,
    source_kind: str,
    source_ref: dict[str, Any],
    faq_jsonld: list[dict[str, Any]] | None = None,
    cta_kind: str = "practice",
    artifact_id: uuid.UUID | None = None,
    status: str = "published",
) -> uuid.UUID:
    post_id = uuid.uuid4()
    emb = pick(bool(embedding), lambda: pgvector_literal(embedding), lambda: None)
    embedding_sql = pick(bool(emb), lambda: "CAST(:emb AS vector)", lambda: "NULL")
    db.execute(
        text(
            f"""
            INSERT INTO qb.seo_post (
              id, slug, title, lede, body_md, format, stream, author_name,
              status, topic_fingerprint, embedding, source_kind, source_ref,
              faq_jsonld, cta_kind, artifact_id, published_at, created_at, updated_at
            ) VALUES (
              :id, :slug, :title, :lede, :body, :fmt, :stream, :author,
              :status, :fp,
              {embedding_sql},
              :sk, CAST(:sref AS jsonb), CAST(:faq AS jsonb), :cta, :aid,
              CASE WHEN :status = 'published' THEN now() ELSE NULL END,
              now(), now()
            )
            """
        ),
        {
            "id": post_id,
            "slug": slug,
            "title": title,
            "lede": lede,
            "body": body_md,
            "fmt": format,
            "stream": stream,
            "author": author_name,
            "status": status,
            "fp": topic_fingerprint,
            **pick(bool(emb), lambda: {"emb": emb}, lambda: {}),
            "sk": source_kind,
            "sref": json.dumps(source_ref),
            "faq": json.dumps(faq_jsonld or []),
            "cta": cta_kind,
            "aid": artifact_id,
        },
    )
    return post_id


def update_post_content(
    db: Session,
    post_id: uuid.UUID,
    *,
    title: str,
    lede: str,
    body_md: str,
    embedding: list[float] | None,
) -> None:
    emb = pick(bool(embedding), lambda: pgvector_literal(embedding), lambda: None)
    embedding_sql = pick(bool(emb), lambda: "embedding = CAST(:emb AS vector),", lambda: "")
    db.execute(
        text(
            f"""
            UPDATE qb.seo_post
            SET title = :title,
                lede = :lede,
                body_md = :body,
                {embedding_sql}
                updated_at = now()
            WHERE id = :id
            """
        ),
        {
            "id": post_id,
            "title": title,
            "lede": lede,
            "body": body_md,
            **pick(bool(emb), lambda: {"emb": emb}, lambda: {}),
        },
    )


def attach_assertions(
    db: Session,
    post_id: uuid.UUID,
    assertion_ids: list[uuid.UUID],
) -> None:
    for i, aid in enumerate(assertion_ids):
        db.execute(
            text(
                """
                INSERT INTO qb.seo_post_assertion (post_id, assertion_id, position)
                VALUES (:p, :a, :pos)
                ON CONFLICT DO NOTHING
                """
            ),
            {"p": post_id, "a": aid, "pos": i},
        )


def set_post_artifact(db: Session, post_id: uuid.UUID, artifact_id: uuid.UUID) -> None:
    db.execute(
        text(
            """
            UPDATE qb.seo_post
            SET artifact_id = :a, updated_at = now()
            WHERE id = :id
            """
        ),
        {"a": artifact_id, "id": post_id},
    )


def set_post_status(db: Session, post_id: uuid.UUID, status: str) -> dict[str, Any] | None:
    def _bad_status() -> None:
        raise ValueError("invalid status")

    pick(status not in {"published", "unpublished", "draft"}, _bad_status, lambda: None)
    db.execute(
        text(
            """
            UPDATE qb.seo_post
            SET status = :st,
                published_at = CASE
                  WHEN :st = 'published' AND published_at IS NULL THEN now()
                  WHEN :st = 'published' THEN published_at
                  ELSE published_at
                END,
                updated_at = now()
            WHERE id = :id
            """
        ),
        {"st": status, "id": post_id},
    )
    db.commit()
    return get_post_by_id(db, post_id, include_internal=True)


def get_post_by_id(
    db: Session, post_id: uuid.UUID, *, include_internal: bool = False
) -> dict[str, Any] | None:
    row = db.execute(
        text("SELECT * FROM qb.seo_post WHERE id = :id"),
        {"id": post_id},
    ).mappings().first()
    return pick(
        not row,
        lambda: None,
        lambda: _public_post(dict(row), include_internal=include_internal),
    )


def get_post_by_slug(
    db: Session, slug: str, *, published_only: bool = True
) -> dict[str, Any] | None:
    q = pick(
        published_only,
        lambda: "SELECT * FROM qb.seo_post WHERE slug = :s AND status = 'published'",
        lambda: "SELECT * FROM qb.seo_post WHERE slug = :s",
    )
    row = db.execute(text(q), {"s": slug}).mappings().first()
    return pick(
        not row,
        lambda: None,
        lambda: _public_post(dict(row), include_internal=False),
    )


def list_published(
    db: Session,
    *,
    stream: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    limit = max(1, min(limit, 50))
    offset = max(0, offset)
    where = ["status = 'published'"]
    params: dict[str, Any] = {"lim": limit, "off": offset}
    pick(
        stream in {"general", "system_design"},
        lambda: (where.append("stream = :stream"), params.__setitem__("stream", stream)),
        lambda: None,
    )
    clause = " AND ".join(where)
    total = db.execute(
        text(f"SELECT COUNT(*)::int FROM qb.seo_post WHERE {clause}"),
        params,
    ).scalar()
    rows = db.execute(
        text(
            f"""
            SELECT id, slug, title, lede, format, stream, author_name,
                   published_at, cta_kind
            FROM qb.seo_post
            WHERE {clause}
            ORDER BY published_at DESC NULLS LAST
            LIMIT :lim OFFSET :off
            """
        ),
        params,
    ).mappings().all()
    items = [
        {
            "id": str(r["id"]),
            "slug": r["slug"],
            "title": r["title"],
            "lede": r["lede"],
            "format": r["format"],
            "stream": r["stream"],
            "author_name": r["author_name"],
            "published_at": _maybe_iso(r.get("published_at")),
            "cta_kind": r["cta_kind"],
        }
        for r in rows
    ]
    return {"items": items, "total": int(total or 0), "limit": limit, "offset": offset}


def list_admin_posts(db: Session, *, limit: int = 40) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT id, slug, title, stream, status, author_name, source_kind,
                   published_at, created_at, topic_fingerprint
            FROM qb.seo_post
            ORDER BY created_at DESC
            LIMIT :lim
            """
        ),
        {"lim": max(1, min(limit, 100))},
    ).mappings().all()
    return [
        {
            "id": str(r["id"]),
            "slug": r["slug"],
            "title": r["title"],
            "stream": r["stream"],
            "status": r["status"],
            "author_name": r["author_name"],
            "source_kind": r["source_kind"],
            "published_at": _maybe_iso(r.get("published_at")),
            "created_at": _maybe_iso(r.get("created_at")),
            "topic_fingerprint": r["topic_fingerprint"],
        }
        for r in rows
    ]


def list_post_questions(db: Session, slug: str, *, limit: int = 20) -> dict[str, Any]:
    post = db.execute(
        text(
            """
            SELECT id, slug, title, status
            FROM qb.seo_post WHERE slug = :s AND status = 'published'
            """
        ),
        {"s": slug},
    ).mappings().first()
    def _empty() -> dict[str, Any]:
        return {"post": None, "items": []}

    def _items() -> dict[str, Any]:
        rows = db.execute(
            text(
                """
                SELECT a.id, a.payload
                FROM qb.seo_post_assertion spa
                JOIN intel.assertion a ON a.id = spa.assertion_id
                WHERE spa.post_id = :pid AND a.status = 'active'
                ORDER BY spa.position ASC
                LIMIT :lim
                """
            ),
            {"pid": post["id"], "lim": max(1, min(limit, 40))},
        ).mappings().all()
        items = []
        for r in rows:
            payload = pick(isinstance(r["payload"], dict), lambda: r["payload"], lambda: {})
            options = payload.get("options") or []
            options = pick(
                isinstance(options, dict),
                lambda: [options[k] for k in sorted(options.keys())],
                lambda: options,
            )
            items.append(
                {
                    "id": str(r["id"]),
                    "question": payload.get("stem") or payload.get("question") or "",
                    "options": list(options),
                    "is_multi": bool(payload.get("is_multi") or payload.get("multi")),
                }
            )
        return {
            "post": {"id": str(post["id"]), "slug": post["slug"], "title": post["title"]},
            "items": items,
        }

    return pick(not post, _empty, _items)


def pick_topic_queue(db: Session, stream: str = "system_design") -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT id, topic_key, stream, title_hint, angle_prompt, priority
            FROM qb.seo_topic_queue
            WHERE stream = :stream
              AND NOT EXISTS (
                SELECT 1 FROM qb.seo_cook_attempt a
                WHERE a.source_kind = 'topic_queue'
                  AND a.source_key = qb.seo_topic_queue.topic_key
              )
            ORDER BY priority ASC, last_used_at ASC NULLS FIRST
            LIMIT 1
            """
        ),
        {"stream": stream},
    ).mappings().first()
    return _row_dict(row)


def mark_topic_used(db: Session, topic_id: uuid.UUID) -> None:
    db.execute(
        text("UPDATE qb.seo_topic_queue SET last_used_at = now() WHERE id = :id"),
        {"id": topic_id},
    )


def pick_unused_sd_problem(db: Session) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT p.id, p.slug, p.title, p.prompt, p.constraints,
                   p.reference_design, p.concept_keys, p.difficulty
            FROM qb.sd_problem p
            WHERE p.published = true
              AND NOT EXISTS (
                SELECT 1 FROM qb.seo_cook_attempt a
                WHERE a.source_kind = 'sd_bank'
                  AND a.source_key = p.slug
              )
              AND NOT EXISTS (
                SELECT 1 FROM qb.seo_post sp
                WHERE sp.status = 'published'
                  AND (
                    sp.source_ref->>'sd_problem_id' = p.id::text
                    OR sp.topic_fingerprint = ('sd:' || p.slug)
                  )
              )
            ORDER BY p.sort_order ASC, p.created_at ASC
            LIMIT 1
            """
        )
    ).mappings().first()
    return _row_dict(row)


def list_newspaper_cook_candidates(db: Session, *, limit: int = 8) -> list[dict[str, Any]]:
    """Unused cook-worthy newspaper PAGES from ready editions.

    A page is split across many token-window chunk rows (one page can have dozens
    of overlapping chunks), so we must collapse to one row per (document, page) by
    concatenating its chunk text in order. Returning raw chunk rows fanned out the
    candidate list: 8 slots could hold only 2 distinct pages, each a 900-char
    fragment lacking the full page's keywords, so the worthiness gate rejected
    pages on incomplete text and burned attempt slots on duplicates.
    """
    rows = db.execute(
        text(
            """
            WITH page_text AS (
                SELECT
                    c.document_id,
                    c.page_start,
                    string_agg(c.text, ' ' ORDER BY c.id) AS text
                FROM qb.document_chunks c
                GROUP BY c.document_id, c.page_start
            )
            SELECT pt.document_id, pt.page_start, pt.text,
                   e.id AS edition_id, e.paper_slug, e.edition_date
            FROM page_text pt
            JOIN qb.newspaper_edition e ON e.document_id = pt.document_id
            JOIN qb.documents d ON d.id = pt.document_id
            WHERE e.status = 'ready'
              AND COALESCE(d.meta->>'newspaper', 'false') IN ('true', 'True')
              AND length(trim(pt.text)) > :min_chars
              AND NOT EXISTS (
                SELECT 1 FROM qb.seo_cook_attempt a
                WHERE a.source_kind = 'newspaper'
                  AND a.source_key = (pt.document_id::text || ':' || pt.page_start::text)
              )
            ORDER BY e.edition_date DESC, pt.page_start ASC
            LIMIT :lim
            """
        ),
        {
            "lim": plan_seo_candidate_query_limit(
                source_kind="newspaper", requested=limit
            ),
            "min_chars": seo_candidate_min_chars("newspaper"),
        },
    ).mappings().all()
    return [dict(r) for r in rows]


def list_upload_candidates(db: Session, *, limit: int = 5) -> list[dict[str, Any]]:
    """Recent ready private uploads not yet attempted for SEO."""
    rows = db.execute(
        text(
            """
            SELECT d.id AS document_id, d.filename, d.meta, d.created_at,
                   (
                     SELECT string_agg(c.text, E'\\n\\n' ORDER BY c.page_start)
                     FROM qb.document_chunks c
                     WHERE c.document_id = d.id
                   ) AS text
            FROM qb.documents d
            WHERE d.status = 'ready'
              AND d.account_id IS NOT NULL
              AND COALESCE(d.meta->>'newspaper', 'false') NOT IN ('true', 'True')
              AND COALESCE(d.meta->>'is_public', 'false') NOT IN ('true', 'True')
              AND COALESCE(d.meta->>'seo_blog', 'false') NOT IN ('true', 'True')
              AND NOT EXISTS (
                SELECT 1 FROM qb.seo_cook_attempt a
                WHERE a.source_kind = 'upload'
                  AND a.source_key = d.id::text
              )
            ORDER BY d.created_at DESC
            LIMIT :lim
            """
        ),
        {"lim": plan_seo_candidate_query_limit(source_kind="upload", requested=limit)},
    ).mappings().all()
    out = []
    for r in rows:
        text_body = (r.get("text") or "").strip()
        pick(
            len(text_body) >= seo_candidate_min_chars("upload"),
            lambda: out.append(dict(r)),
            lambda: None,
        )
    return out


def list_sitemap_slugs(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT slug, published_at, updated_at
            FROM qb.seo_post
            WHERE status = 'published'
            ORDER BY published_at DESC
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def list_newspaper_blog_sitemap(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT e.paper_slug, e.edition_date, e.blog_published_at, p.updated_at
            FROM qb.newspaper_edition e
            JOIN qb.seo_post p ON p.id = e.blog_post_id
            WHERE e.blog_status = 'published'
              AND p.status = 'published'
            ORDER BY e.edition_date DESC
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def aggregate_document_text(db: Session, document_id: uuid.UUID) -> str:
    row = db.execute(
        text(
            """
            SELECT string_agg(c.text, E'\\n\\n' ORDER BY c.page_start) AS body
            FROM qb.document_chunks c
            WHERE c.document_id = :d
            """
        ),
        {"d": document_id},
    ).first()
    return pick(bool(row) and bool(row[0]), lambda: str(row[0]), lambda: "")


def list_document_page_texts(
    db: Session, document_id: uuid.UUID
) -> list[dict[str, Any]]:
    """One row per page with chunk text collapsed in reading order."""
    rows = db.execute(
        text(
            """
            SELECT
                c.page_start,
                string_agg(c.text, ' ' ORDER BY c.id) AS text
            FROM qb.document_chunks c
            WHERE c.document_id = :d
            GROUP BY c.page_start
            ORDER BY c.page_start
            """
        ),
        {"d": document_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_edition_blog_post(
    db: Session,
    *,
    paper_slug: str,
    edition_date: date,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT p.*, e.id AS edition_id, e.paper_title, e.edition_date
            FROM qb.newspaper_edition e
            JOIN qb.seo_post p ON p.id = e.blog_post_id
            WHERE e.paper_slug = :slug
              AND e.edition_date = :day
              AND e.blog_post_id IS NOT NULL
              AND p.status = 'published'
            """
        ),
        {"slug": paper_slug, "day": edition_date},
    ).mappings().first()
    def _missing() -> None:
        return None

    def _post() -> dict[str, Any]:
        post = _public_post(dict(row), include_internal=False)
        post["edition_id"] = str(row["edition_id"])
        post["paper_slug"] = paper_slug
        post["paper_title"] = row.get("paper_title") or paper_slug
        post["edition_date"] = row["edition_date"].isoformat()
        post["practice_href"] = f"/practice/newspaper/e/{row['edition_id']}"
        return post

    return pick(not row, _missing, _post)


def list_edition_blog_archive(
    db: Session,
    *,
    paper_slug: str,
    limit: int = 30,
) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT e.id AS edition_id, e.edition_date, e.blog_published_at,
                   p.slug, p.title, p.lede
            FROM qb.newspaper_edition e
            JOIN qb.seo_post p ON p.id = e.blog_post_id
            WHERE e.paper_slug = :slug
              AND e.blog_status = 'published'
              AND p.status = 'published'
            ORDER BY e.edition_date DESC
            LIMIT :lim
            """
        ),
        {"slug": paper_slug, "lim": max(1, min(limit, 60))},
    ).mappings().all()
    return [
        {
            "edition_id": str(r["edition_id"]),
            "edition_date": r["edition_date"].isoformat(),
            "slug": r["slug"],
            "title": r["title"],
            "lede": r["lede"],
            "published_at": _maybe_iso(r.get("blog_published_at")),
            "href": f"/learn/newspaper/{paper_slug}/{r['edition_date'].isoformat()}",
        }
        for r in rows
    ]


def _public_post(row: dict[str, Any], *, include_internal: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(row["id"]),
        "slug": row["slug"],
        "title": row["title"],
        "lede": row["lede"],
        "body_md": row["body_md"],
        "format": row["format"],
        "stream": row["stream"],
        "author_name": row["author_name"],
        "status": row["status"],
        "faq_jsonld": row.get("faq_jsonld") or [],
        "cta_kind": row.get("cta_kind") or "practice",
        "published_at": _maybe_iso(row.get("published_at")),
        "created_at": pick(
            isinstance(row.get("created_at"), datetime),
            lambda: row["created_at"].isoformat(),
            lambda: row.get("created_at"),
        ),
    }
    pick(
        include_internal,
        lambda: out.update(
            {
                "topic_fingerprint": row.get("topic_fingerprint"),
                "source_kind": row.get("source_kind"),
                "source_ref": row.get("source_ref") or {},
                "artifact_id": pick(
                    bool(row.get("artifact_id")),
                    lambda: str(row["artifact_id"]),
                    lambda: None,
                ),
            }
        ),
        lambda: None,
    )
    return out
