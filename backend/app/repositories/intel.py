"""Intel schema repositories (raw SQL)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


_concept_id_store: dict[str, uuid.UUID] = {}
_source_id_store: dict[str, uuid.UUID] = {}


def _concept_id(db: Session, uri: str) -> uuid.UUID:
    cached = _concept_id_store.get(uri)
    if cached is not None:
        return cached
    row = db.execute(
        text("SELECT id FROM intel.concept WHERE uri = :uri"),
        {"uri": uri},
    ).first()
    if not row:
        raise ValueError(f"Missing concept seed: {uri}")
    _concept_id_store[uri] = row[0]
    return row[0]


def concept_id(db: Session, uri: str) -> uuid.UUID:
    """Public accessor for a seeded vocab concept's id by uri (cached).

    Raises ValueError if the concept is not seeded — callers depend on the
    vocabulary existing (see scripts/seed_question_vocab.py).
    """
    return _concept_id(db, uri)


def _source_id(db: Session, slug: str) -> uuid.UUID:
    cached = _source_id_store.get(slug)
    if cached is not None:
        return cached
    row = db.execute(
        text("SELECT id FROM intel.source WHERE slug = :slug"),
        {"slug": slug},
    ).first()
    if not row:
        raise ValueError(f"Missing source seed: {slug}")
    _source_id_store[slug] = row[0]
    return row[0]


def create_activity(
    db: Session,
    *,
    type_uri: str,
    agent: str,
    source_slug: str | None = None,
    stats: dict[str, Any] | None = None,
) -> uuid.UUID:
    activity_id = uuid.uuid4()
    source_id = _source_id(db, source_slug) if source_slug else None
    db.execute(
        text(
            """
            INSERT INTO intel.activity (id, type_concept_id, source_id, agent, status, stats)
            VALUES (:id, :type_id, :source_id, :agent, 'running', CAST(:stats AS jsonb))
            """
        ),
        {
            "id": activity_id,
            "type_id": _concept_id(db, type_uri),
            "source_id": source_id,
            "agent": agent,
            "stats": json.dumps(stats or {}),
        },
    )
    return activity_id


def update_activity(
    db: Session,
    activity_id: uuid.UUID,
    *,
    status: str | None = None,
    stats: dict[str, Any] | None = None,
    error_summary: str | None = None,
    finished: bool = False,
) -> None:
    parts = []
    params: dict[str, Any] = {"id": activity_id}
    if status:
        parts.append("status = :status")
        params["status"] = status
    if stats is not None:
        parts.append("stats = stats || CAST(:stats AS jsonb)")
        params["stats"] = json.dumps(stats)
    if error_summary is not None:
        parts.append("error_summary = :error")
        params["error"] = error_summary
    if finished:
        parts.append("finished_at = :finished_at")
        params["finished_at"] = datetime.now(timezone.utc)
    if not parts:
        return
    db.execute(
        text(f"UPDATE intel.activity SET {', '.join(parts)} WHERE id = :id"),
        params,
    )


def get_activity(db: Session, activity_id: uuid.UUID) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT id, status, stats, error_summary, agent, started_at, finished_at
            FROM intel.activity WHERE id = :id
            """
        ),
        {"id": activity_id},
    ).mappings().first()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Practice library — concept graph (Wikidata QID-backed intel.entity rows)
# ---------------------------------------------------------------------------

_CONCEPT_TYPE_URI = "/vocab/entity/concept"
_TESTS_ROLE_URI = "/vocab/role/tests"
_SUBCLASS_OF_URI = "/vocab/relation/subclass_of"


def get_or_create_concept_entity(
    db: Session,
    qid: str,
    label: str,
    description: str | None = None,
) -> uuid.UUID:
    """Upsert an intel.entity for a Wikidata concept, keyed by `wikidata:<qid>`.

    Creates the entity + a `wikidata` scheme identifier + a label. Re-touching
    an existing concept refreshes its label/description. Idempotent on canonical_uri.
    """
    canonical_uri = f"wikidata:{qid}"
    now = datetime.now(timezone.utc)
    type_id = _concept_id(db, _CONCEPT_TYPE_URI)
    normalized = (label or qid).strip().lower()

    row = db.execute(
        text(
            """
            INSERT INTO intel.entity (type_concept_id, canonical_uri, status)
            VALUES (:type_id, :uri, 'active')
            ON CONFLICT (canonical_uri) DO UPDATE SET canonical_uri = EXCLUDED.canonical_uri
            RETURNING id
            """
        ),
        {"type_id": type_id, "uri": canonical_uri},
    ).first()
    entity_id = row[0]

    # Wikidata identifier (scheme 'wikidata', value QID) — idempotent on the
    # active-unique partial index (scheme, value, jurisdiction).
    db.execute(
        text(
            """
            INSERT INTO intel.entity_identifier (entity_id, scheme, value, valid_from)
            VALUES (:entity_id, 'wikidata', :value, :now)
            ON CONFLICT DO NOTHING
            """
        ),
        {"entity_id": entity_id, "value": qid, "now": now},
    )

    # Label — replace any active label with the fresh one for this concept.
    db.execute(
        text(
            """
            UPDATE intel.entity_label SET valid_to = :now
            WHERE entity_id = :entity_id AND valid_to IS NULL
            """
        ),
        {"entity_id": entity_id, "now": now},
    )
    db.execute(
        text(
            """
            INSERT INTO intel.entity_label
              (entity_id, label, label_normalized, language, valid_from)
            VALUES (:entity_id, :label, :normalized, 'en', :now)
            """
        ),
        {
            "entity_id": entity_id,
            "label": (label or qid)[:500],
            "normalized": normalized[:500],
            "now": now,
        },
    )

    # Note: the human-readable description from Wikidata is surfaced back to the
    # UI by the Wikidata service at read time; it is not stored on the entity to
    # keep the knowledge graph clean (entities are concepts, not prose).

    return entity_id


_PERSON_TYPE_URI = "/vocab/entity/person"


def get_or_create_account_entity(
    db: Session,
    account_id: uuid.UUID,
    username: str,
) -> uuid.UUID:
    """Link a qb.account to a stable intel.entity (person) for measurements.

    Idempotent — safe on signup gaps, OAuth completes, and concurrent first grades.
    """
    row = db.execute(
        text("SELECT entity_id FROM qb.account_entity WHERE account_id = :id"),
        {"id": account_id},
    ).first()
    if row:
        return row[0]

    canonical_uri = f"qb://account/{account_id}"
    now = datetime.now(timezone.utc)
    type_id = _concept_id(db, _PERSON_TYPE_URI)
    label = (username or str(account_id))[:500]
    normalized = label.strip().lower()[:500]

    entity_row = db.execute(
        text(
            """
            INSERT INTO intel.entity (type_concept_id, canonical_uri, status)
            VALUES (:type_id, :uri, 'active')
            ON CONFLICT (canonical_uri) DO UPDATE SET canonical_uri = EXCLUDED.canonical_uri
            RETURNING id
            """
        ),
        {"type_id": type_id, "uri": canonical_uri},
    ).first()
    entity_id = entity_row[0]

    db.execute(
        text(
            """
            UPDATE intel.entity_label SET valid_to = :now
            WHERE entity_id = :entity_id AND valid_to IS NULL
            """
        ),
        {"entity_id": entity_id, "now": now},
    )
    db.execute(
        text(
            """
            INSERT INTO intel.entity_label
              (entity_id, label, label_normalized, language, valid_from)
            VALUES (:entity_id, :label, :normalized, 'en', :now)
            """
        ),
        {
            "entity_id": entity_id,
            "label": label,
            "normalized": normalized,
            "now": now,
        },
    )

    db.execute(
        text(
            """
            INSERT INTO qb.account_entity (account_id, entity_id)
            VALUES (:account_id, :entity_id)
            ON CONFLICT (account_id) DO NOTHING
            """
        ),
        {"account_id": account_id, "entity_id": entity_id},
    )

    linked = db.execute(
        text("SELECT entity_id FROM qb.account_entity WHERE account_id = :id"),
        {"id": account_id},
    ).first()
    if not linked:
        raise RuntimeError(f"account_entity link missing for account {account_id}")
    return linked[0]


def link_assertion_concept(
    db: Session,
    assertion_id: uuid.UUID,
    entity_id: uuid.UUID,
    role_uri: str = _TESTS_ROLE_URI,
) -> None:
    """Link an assertion (question) to a concept entity it tests. Idempotent."""
    db.execute(
        text(
            """
            INSERT INTO intel.assertion_participant (assertion_id, entity_id, role_concept_id)
            VALUES (:assertion_id, :entity_id, :role_id)
            ON CONFLICT DO NOTHING
            """
        ),
        {
            "assertion_id": assertion_id,
            "entity_id": entity_id,
            "role_id": _concept_id(db, role_uri),
        },
    )


def link_concept_parent(
    db: Session,
    child_entity_id: uuid.UUID,
    parent_entity_id: uuid.UUID,
    relation_uri: str = _SUBCLASS_OF_URI,
) -> None:
    """Record a concept hierarchy edge (child subclass_of parent). Idempotent."""
    now = datetime.now(timezone.utc)
    db.execute(
        text(
            """
            UPDATE intel.relation
            SET valid_to = :now
            WHERE from_entity_id = :child
              AND type_concept_id = :rel_id
              AND valid_to IS NULL
            """
        ),
        {"child": child_entity_id, "rel_id": _concept_id(db, relation_uri), "now": now},
    )
    db.execute(
        text(
            """
            INSERT INTO intel.relation
              (from_entity_id, to_entity_id, type_concept_id, valid_from)
            VALUES (:child, :parent, :rel_id, :now)
            ON CONFLICT DO NOTHING
            """
        ),
        {
            "child": child_entity_id,
            "parent": parent_entity_id,
            "rel_id": _concept_id(db, relation_uri),
            "now": now,
        },
    )


def get_concept_entity_by_qid(db: Session, qid: str) -> uuid.UUID | None:
    """Look up an existing concept entity by its Wikidata QID, or None."""
    row = db.execute(
        text(
            """
            SELECT e.id
            FROM intel.entity e
            JOIN intel.entity_identifier i
              ON i.entity_id = e.id AND i.valid_to IS NULL
            WHERE i.scheme = 'wikidata' AND i.value = :qid
            LIMIT 1
            """
        ),
        {"qid": qid},
    ).first()
    return row[0] if row else None


def count_concept_questions(
    db: Session,
    entity_id: uuid.UUID,
    *,
    public_only: bool = False,
    owner_account_id: uuid.UUID | None = None,
) -> int:
    """Active MCQ assertions linked to a concept via the 'tests' role.

    When ``public_only`` is set, restrict to assertions whose underlying document
    is open to everyone (account_id NULL + is_public/is_demo) or owned by
    ``owner_account_id``. This keeps private documents' questions out of the
    unauthenticated practice-concept listing — otherwise they'd be listed but
    ungradeable (and leak question stems from other users' private uploads).
    """
    joins = ""
    where = ""
    params: dict[str, Any] = {
        "entity_id": entity_id,
        "role_id": _concept_id(db, _TESTS_ROLE_URI),
    }
    if public_only:
        joins = "LEFT JOIN qb.documents d ON d.id = CAST(a.payload->>'artifact_id' AS uuid)"
        where = (
            " AND ("
            "  d.id IS NULL"  # assertion not tied to a doc (e.g. seed bank)
            "  OR d.account_id IS NULL AND COALESCE(d.meta->>'is_public', d.meta->>'is_demo') IS NOT NULL"
            + (" OR d.account_id = :owner_id" if owner_account_id else "")
            + ")"
        )
        if owner_account_id:
            params["owner_id"] = owner_account_id
    row = db.execute(
        text(
            f"""
            SELECT count(*)
            FROM intel.assertion_participant ap
            JOIN intel.assertion a ON a.id = ap.assertion_id
            {joins}
            WHERE ap.entity_id = :entity_id
              AND ap.role_concept_id = :role_id
              AND a.status = 'active'{where}
            """
        ),
        params,
    ).first()
    return int(row[0]) if row else 0


def get_concept_questions(
    db: Session,
    entity_id: uuid.UUID,
    limit: int = 20,
    offset: int = 0,
    *,
    public_only: bool = False,
    owner_account_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """Active MCQ assertions testing a concept, newest first.

    See ``count_concept_questions`` for the ``public_only`` access filter.
    """
    joins = ""
    where = ""
    params: dict[str, Any] = {
        "entity_id": entity_id,
        "role_id": _concept_id(db, _TESTS_ROLE_URI),
        "limit": limit,
        "offset": offset,
    }
    if public_only:
        joins = "LEFT JOIN qb.documents d ON d.id = CAST(a.payload->>'artifact_id' AS uuid)"
        where = (
            " AND ("
            "  d.id IS NULL"
            "  OR d.account_id IS NULL AND COALESCE(d.meta->>'is_public', d.meta->>'is_demo') IS NOT NULL"
            + (" OR d.account_id = :owner_id" if owner_account_id else "")
            + ")"
        )
        if owner_account_id:
            params["owner_id"] = owner_account_id
    rows = db.execute(
        text(
            f"""
            SELECT a.id, a.title, a.summary, a.payload, a.status
            FROM intel.assertion_participant ap
            JOIN intel.assertion a ON a.id = ap.assertion_id
            {joins}
            WHERE ap.entity_id = :entity_id
              AND ap.role_concept_id = :role_id
              AND a.status = 'active'{where}
            ORDER BY a.recorded_at DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    ).mappings().all()
    return [dict(r) for r in rows]


def get_concept_relations(
    db: Session,
    entity_id: uuid.UUID,
    direction: str,
    relation_uri: str = _SUBCLASS_OF_URI,
) -> list[dict[str, Any]]:
    """Related concepts. direction='parents' (this subclass_of others) or
    'children' (others subclass_of this)."""
    rel_id = _concept_id(db, relation_uri)
    if direction == "parents":
        select_sql = """
            SELECT e.id, e.canonical_uri, l.label,
                   i.value AS qid
            FROM intel.relation r
            JOIN intel.entity e ON e.id = r.to_entity_id
            LEFT JOIN LATERAL (
              SELECT label FROM intel.entity_label
              WHERE entity_id = e.id AND valid_to IS NULL
              ORDER BY valid_from DESC LIMIT 1
            ) l ON true
            LEFT JOIN LATERAL (
              SELECT value FROM intel.entity_identifier
              WHERE entity_id = e.id AND scheme = 'wikidata' AND valid_to IS NULL
              LIMIT 1
            ) i ON true
            WHERE r.from_entity_id = :entity_id AND r.type_concept_id = :rel_id
              AND r.valid_to IS NULL
        """
    else:
        select_sql = """
            SELECT e.id, e.canonical_uri, l.label,
                   i.value AS qid
            FROM intel.relation r
            JOIN intel.entity e ON e.id = r.from_entity_id
            LEFT JOIN LATERAL (
              SELECT label FROM intel.entity_label
              WHERE entity_id = e.id AND valid_to IS NULL
              ORDER BY valid_from DESC LIMIT 1
            ) l ON true
            LEFT JOIN LATERAL (
              SELECT value FROM intel.entity_identifier
              WHERE entity_id = e.id AND scheme = 'wikidata' AND valid_to IS NULL
              LIMIT 1
            ) i ON true
            WHERE r.to_entity_id = :entity_id AND r.type_concept_id = :rel_id
              AND r.valid_to IS NULL
        """
    rows = db.execute(
        text(select_sql), {"entity_id": entity_id, "rel_id": rel_id}
    ).mappings().all()
    return [
        {
            "qid": r.get("qid"),
            "label": r.get("label"),
            "entity_id": str(r["id"]),
        }
        for r in rows
    ]


def get_concept_label(db: Session, entity_id: uuid.UUID) -> str | None:
    row = db.execute(
        text(
            """
            SELECT label FROM intel.entity_label
            WHERE entity_id = :entity_id AND valid_to IS NULL
            ORDER BY valid_from DESC LIMIT 1
            """
        ),
        {"entity_id": entity_id},
    ).first()
    return row[0] if row else None
