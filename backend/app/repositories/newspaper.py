"""Raw SQL for newspaper editions, settings, and aliases."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

RETENTION_DAYS = 30


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return slug[:64] or "paper"


def get_settings(db: Session) -> dict[str, Any]:
    row = db.execute(
        text(
            """
            SELECT channel_ref, channel_label, sync_cursor, allowlist_only, updated_at
            FROM qb.newspaper_settings WHERE id = 1
            """
        )
    ).mappings().first()
    if not row:
        db.execute(
            text(
                """
                INSERT INTO qb.newspaper_settings (id, channel_ref, channel_label)
                VALUES (1, '', '')
                ON CONFLICT (id) DO NOTHING
                """
            )
        )
        db.commit()
        return {
            "channel_ref": "",
            "channel_label": "",
            "sync_cursor": None,
            "allowlist_only": False,
            "updated_at": None,
        }
    out = dict(row)
    out["allowlist_only"] = bool(out.get("allowlist_only"))
    return out


def set_channel(db: Session, *, channel_ref: str, channel_label: str = "") -> dict[str, Any]:
    db.execute(
        text(
            """
            INSERT INTO qb.newspaper_settings (id, channel_ref, channel_label, sync_cursor, updated_at)
            VALUES (1, :ref, :label, NULL, now())
            ON CONFLICT (id) DO UPDATE SET
              channel_ref = EXCLUDED.channel_ref,
              channel_label = EXCLUDED.channel_label,
              sync_cursor = NULL,
              updated_at = now()
            """
        ),
        {"ref": channel_ref.strip(), "label": (channel_label or channel_ref).strip()},
    )
    db.commit()
    return get_settings(db)


def update_channel_ref_keep_cursor(
    db: Session, *, channel_ref: str, channel_label: str | None = None
) -> dict[str, Any]:
    """Persist a discovered peer id without wiping sync_cursor (unlike set_channel)."""
    ref = channel_ref.strip()
    if not ref:
        return get_settings(db)
    if channel_label is None:
        db.execute(
            text(
                """
                UPDATE qb.newspaper_settings
                SET channel_ref = :ref, updated_at = now()
                WHERE id = 1
                """
            ),
            {"ref": ref},
        )
    else:
        db.execute(
            text(
                """
                UPDATE qb.newspaper_settings
                SET channel_ref = :ref, channel_label = :label, updated_at = now()
                WHERE id = 1
                """
            ),
            {"ref": ref, "label": channel_label.strip()},
        )
    db.commit()
    return get_settings(db)


def set_sync_cursor(db: Session, cursor: int | None) -> None:
    db.execute(
        text("UPDATE qb.newspaper_settings SET sync_cursor = :c, updated_at = now() WHERE id = 1"),
        {"c": cursor},
    )
    db.commit()


def resolve_alias(db: Session, raw_key: str) -> tuple[str, str] | None:
    key = re.sub(r"[^a-z0-9]+", "", (raw_key or "").lower())
    if not key:
        return None
    row = db.execute(
        text(
            """
            SELECT paper_slug, paper_title FROM qb.newspaper_paper_alias
            WHERE alias_key = :k
            """
        ),
        {"k": key},
    ).first()
    if not row:
        return None
    return str(row[0]), str(row[1])


def upsert_alias(db: Session, *, alias_key: str, paper_slug: str, paper_title: str) -> None:
    key = re.sub(r"[^a-z0-9]+", "", alias_key.lower())
    if not key:
        return
    db.execute(
        text(
            """
            INSERT INTO qb.newspaper_paper_alias (alias_key, paper_slug, paper_title, updated_at)
            VALUES (:k, :slug, :title, now())
            ON CONFLICT (alias_key) DO UPDATE SET
              paper_slug = EXCLUDED.paper_slug,
              paper_title = EXCLUDED.paper_title,
              updated_at = now()
            """
        ),
        {"k": key, "slug": paper_slug, "title": paper_title},
    )


def delete_alias(db: Session, alias_key: str) -> None:
    key = re.sub(r"[^a-z0-9]+", "", (alias_key or "").lower())
    if not key:
        return
    db.execute(
        text("DELETE FROM qb.newspaper_paper_alias WHERE alias_key = :k"),
        {"k": key},
    )


def delete_all_aliases(db: Session) -> int:
    result = db.execute(text("DELETE FROM qb.newspaper_paper_alias"))
    return int(result.rowcount or 0)


def delete_edition_row(db: Session, edition_id: uuid.UUID) -> None:
    """Hard-delete edition row so (paper, day) can be re-ingested."""
    db.execute(
        text("DELETE FROM qb.newspaper_edition WHERE id = :id"),
        {"id": edition_id},
    )


def get_edition(db: Session, edition_id: uuid.UUID) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT id, paper_slug, paper_title, edition_date, document_id,
                   telegram_msg_id, location_raw, status, created_at, updated_at
            FROM qb.newspaper_edition WHERE id = :id
            """
        ),
        {"id": edition_id},
    ).mappings().first()
    return dict(row) if row else None


def get_edition_by_paper_day(
    db: Session, *, paper_slug: str, edition_date: date
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT id, paper_slug, paper_title, edition_date, document_id,
                   telegram_msg_id, location_raw, status, created_at, updated_at
            FROM qb.newspaper_edition
            WHERE paper_slug = :slug AND edition_date = :d
              AND status <> 'purged'
            """
        ),
        {"slug": paper_slug, "d": edition_date},
    ).mappings().first()
    return dict(row) if row else None


def insert_edition(
    db: Session,
    *,
    paper_slug: str,
    paper_title: str,
    edition_date: date,
    document_id: uuid.UUID | None,
    telegram_msg_id: int | None,
    location_raw: str,
    status: str = "pending",
) -> uuid.UUID:
    edition_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO qb.newspaper_edition (
              id, paper_slug, paper_title, edition_date, document_id,
              telegram_msg_id, location_raw, status
            ) VALUES (
              :id, :slug, :title, :d, :doc, :msg, :loc, :status
            )
            """
        ),
        {
            "id": edition_id,
            "slug": paper_slug,
            "title": paper_title,
            "d": edition_date,
            "doc": document_id,
            "msg": telegram_msg_id,
            "loc": location_raw or "",
            "status": status,
        },
    )
    return edition_id


def update_edition_status(
    db: Session,
    edition_id: uuid.UUID,
    *,
    status: str,
    document_id: uuid.UUID | None = None,
) -> None:
    if document_id is not None:
        db.execute(
            text(
                """
                UPDATE qb.newspaper_edition
                SET status = :status, document_id = :doc, updated_at = now()
                WHERE id = :id
                """
            ),
            {"id": edition_id, "status": status, "doc": document_id},
        )
    else:
        db.execute(
            text(
                """
                UPDATE qb.newspaper_edition
                SET status = :status, updated_at = now()
                WHERE id = :id
                """
            ),
            {"id": edition_id, "status": status},
        )


def list_days_for_paper(db: Session, *, paper_slug: str, since: date) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT id, paper_title, edition_date, status, document_id, location_raw
            FROM qb.newspaper_edition
            WHERE paper_slug = :slug
              AND edition_date >= :since
              AND status IN ('ready', 'indexing', 'pending')
            ORDER BY edition_date DESC
            """
        ),
        {"slug": paper_slug, "since": since},
    ).mappings().all()
    return [dict(r) for r in rows]


def list_expired_ready(db: Session, *, before: date) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT id, document_id, paper_slug, edition_date, status
            FROM qb.newspaper_edition
            WHERE edition_date < :before
              AND status <> 'purged'
            ORDER BY edition_date ASC
            LIMIT 50
            """
        ),
        {"before": before},
    ).mappings().all()
    return [dict(r) for r in rows]


def retention_cutoff(now: datetime | None = None) -> date:
    now = now or datetime.now(timezone.utc)
    return (now.astimezone(timezone.utc) - timedelta(days=RETENTION_DAYS)).date()


def make_paper_identity(title: str) -> tuple[str, str]:
    title = re.sub(r"\s+", " ", (title or "").strip()) or "Newspaper"
    return _slugify(title), title


def set_allowlist_only(db: Session, *, allowlist_only: bool) -> dict[str, Any]:
    db.execute(
        text(
            """
            UPDATE qb.newspaper_settings
            SET allowlist_only = :v, updated_at = now()
            WHERE id = 1
            """
        ),
        {"v": allowlist_only},
    )
    db.commit()
    return get_settings(db)


def upsert_brand(
    db: Session,
    *,
    paper_slug: str,
    paper_title: str,
    enabled_if_new: bool = False,
) -> None:
    db.execute(
        text(
            """
            INSERT INTO qb.newspaper_brand (paper_slug, paper_title, enabled, first_seen_at, updated_at)
            VALUES (:slug, :title, :enabled, now(), now())
            ON CONFLICT (paper_slug) DO UPDATE SET
              paper_title = EXCLUDED.paper_title,
              updated_at = now()
            """
        ),
        {"slug": paper_slug, "title": paper_title, "enabled": enabled_if_new},
    )


def set_brand_enabled(db: Session, *, paper_slug: str, enabled: bool) -> dict[str, Any] | None:
    db.execute(
        text(
            """
            UPDATE qb.newspaper_brand
            SET enabled = :e, updated_at = now()
            WHERE paper_slug = :slug
            """
        ),
        {"slug": paper_slug, "e": enabled},
    )
    db.commit()
    row = db.execute(
        text(
            """
            SELECT paper_slug, paper_title, enabled, first_seen_at, updated_at
            FROM qb.newspaper_brand WHERE paper_slug = :slug
            """
        ),
        {"slug": paper_slug},
    ).mappings().first()
    return dict(row) if row else None


def list_brands(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT paper_slug, paper_title, enabled, first_seen_at, updated_at
            FROM qb.newspaper_brand
            ORDER BY paper_title
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def is_brand_allowed(db: Session, paper_slug: str) -> bool:
    """When allowlist_only is off → all papers allowed. When on → must be enabled."""
    settings = get_settings(db)
    if not settings.get("allowlist_only"):
        return True
    row = db.execute(
        text(
            """
            SELECT enabled FROM qb.newspaper_brand WHERE paper_slug = :slug
            """
        ),
        {"slug": paper_slug},
    ).first()
    return bool(row and row[0])


def list_papers(db: Session, *, since: date, allowlist_only: bool | None = None) -> list[dict[str, Any]]:
    if allowlist_only is None:
        allowlist_only = bool(get_settings(db).get("allowlist_only"))
    filter_sql = ""
    if allowlist_only:
        filter_sql = """
              AND EXISTS (
                SELECT 1 FROM qb.newspaper_brand b
                WHERE b.paper_slug = e.paper_slug AND b.enabled = true
              )
        """
    rows = db.execute(
        text(
            f"""
            SELECT e.paper_slug, e.paper_title,
                   COUNT(*) FILTER (WHERE e.status = 'ready')::int AS ready_days,
                   MAX(e.edition_date) FILTER (WHERE e.status = 'ready') AS latest_date
            FROM qb.newspaper_edition e
            WHERE e.edition_date >= :since
              AND e.status IN ('ready', 'indexing', 'pending')
              {filter_sql}
            GROUP BY e.paper_slug, e.paper_title
            HAVING COUNT(*) FILTER (WHERE e.status = 'ready') > 0
               OR COUNT(*) FILTER (WHERE e.status IN ('indexing', 'pending')) > 0
            ORDER BY e.paper_title
            """
        ),
        {"since": since},
    ).mappings().all()
    return [dict(r) for r in rows]
