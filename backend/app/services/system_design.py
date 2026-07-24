"""System Design mastery — skill path, cases, grade + teach-gap + next.

Separate product from Interview. Raw SQL on qb.sd_concept / sd_problem / sd_session.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.llm_json import extract_json_obj
from app.services.llm_router import complete_chat

BUILDING_BLOCKS = [
    "Client",
    "API / Gateway",
    "Load balancer",
    "App servers",
    "Cache",
    "Database",
    "Queue",
    "Object storage",
    "CDN",
    "Search index",
    "Workers",
]

CONCEPTS: list[dict[str, Any]] = [
    {
        "key": "requirements",
        "title": "Requirements & constraints",
        "blurb": "Clarify functional needs, scale, and what you will not build.",
        "sort_order": 10,
        "prerequisites": [],
    },
    {
        "key": "apis",
        "title": "APIs & boundaries",
        "blurb": "Define clear interfaces between clients, services, and data.",
        "sort_order": 20,
        "prerequisites": ["requirements"],
    },
    {
        "key": "data",
        "title": "Data & consistency",
        "blurb": "Choose storage and consistency for the real read/write patterns.",
        "sort_order": 30,
        "prerequisites": ["apis"],
    },
    {
        "key": "caching",
        "title": "Caching",
        "blurb": "Cut latency and load without serving lies.",
        "sort_order": 40,
        "prerequisites": ["data"],
    },
    {
        "key": "queues",
        "title": "Queues & async",
        "blurb": "Decouple work that should not block the user path.",
        "sort_order": 50,
        "prerequisites": ["apis"],
    },
    {
        "key": "sharding",
        "title": "Sharding & partitioning",
        "blurb": "Split data and traffic when one machine is not enough.",
        "sort_order": 60,
        "prerequisites": ["data"],
    },
    {
        "key": "availability",
        "title": "Availability & failure",
        "blurb": "Survive machine and zone loss without silent corruption.",
        "sort_order": 70,
        "prerequisites": ["data", "caching"],
    },
    {
        "key": "estimation",
        "title": "Capacity estimation",
        "blurb": "Back-of-envelope QPS, storage, and bandwidth before drawing boxes.",
        "sort_order": 80,
        "prerequisites": ["requirements"],
    },
]

# Obsessive starter bank — quality over quantity. concept_keys drive the path.
PROBLEMS: list[dict[str, Any]] = [
    {
        "slug": "url-shortener",
        "title": "URL shortener",
        "difficulty": "easy",
        "sort_order": 10,
        "concept_keys": ["requirements", "apis", "data"],
        "prompt": (
            "Design a URL shortener like bit.ly. Users submit a long URL and get a short link "
            "that redirects. Think about uniqueness, read-heavy traffic, and what happens if "
            "someone guesses short codes."
        ),
        "constraints": "100M new links/month · 10:1 read:write · 99.9% availability · latency < 100ms for redirects",
        "rubric_hints": {
            "watch": ["collision strategy", "key generation", "redirect hot path", "analytics async"]
        },
        "reference_design": (
            "Generate opaque IDs (base62 of a counter or hash+check), store long→short in a primary KV/SQL, "
            "cache hot redirects in Redis, serve 301/302 from an edge-friendly API. Writes go to primary; "
            "click analytics enqueue to a queue. Rate-limit create. Do not expose sequential IDs without care."
        ),
    },
    {
        "slug": "pastebin",
        "title": "Pastebin",
        "difficulty": "easy",
        "sort_order": 20,
        "concept_keys": ["requirements", "apis", "data", "caching"],
        "prompt": (
            "Design a pastebin: create a text paste, get a shareable URL, optional expiry and "
            "burn-after-read. Content can be large; most pastes are written once and read a few times."
        ),
        "constraints": "50M pastes · average 20KB · optional TTL · public by default",
        "rubric_hints": {"watch": ["blob storage vs DB", "TTL cleanup", "hot paste cache"]},
        "reference_design": (
            "Metadata in SQL (id, owner, expiry, burn flag); body in object storage or TOAST. "
            "CDN/cache for popular pastes. Background job deletes expired. Burn-after-read needs "
            "atomic read+delete or soft-delete race handling."
        ),
    },
    {
        "slug": "rate-limiter",
        "title": "Distributed rate limiter",
        "difficulty": "medium",
        "sort_order": 30,
        "concept_keys": ["apis", "caching", "estimation"],
        "prompt": (
            "Design a rate limiter used by many API gateways. Support per-user and per-IP limits "
            "with sliding or token-bucket semantics across multiple gateway instances."
        ),
        "constraints": "100k QPS decisions · multi-region gateways · eventual consistency of counts OK if bounded",
        "rubric_hints": {"watch": ["central Redis vs local", "clock skew", "fail open vs closed"]},
        "reference_design": (
            "Token bucket or sliding window in Redis (INCR/EXPIRE or sorted sets). Gateways call "
            "a local library with Redis backend; sticky keys by user/IP. On Redis failure choose "
            "fail-open (availability) or fail-closed (safety) explicitly. Estimate memory: "
            "active keys × state size."
        ),
    },
    {
        "slug": "news-feed",
        "title": "News feed",
        "difficulty": "medium",
        "sort_order": 40,
        "concept_keys": ["apis", "data", "caching", "queues"],
        "prompt": (
            "Design a Twitter/X-style home feed: follow graph, post creation, and a timeline of "
            "posts from people you follow. Celebrity fanout is the hard part."
        ),
        "constraints": "500M users · 200M DAU · celebrities with 30M followers · feed load < 200ms p99",
        "rubric_hints": {"watch": ["push vs pull", "hybrid fanout", "ranking later"]},
        "reference_design": (
            "Hybrid: pull for celebrities (on read, merge recent posts), push fanout-on-write for "
            "normal users into per-user feed caches. Posts in a write-optimized store; graph in "
            "graph DB or sharded edges. Async workers for fanout. Cache home feed lists in Redis."
        ),
    },
    {
        "slug": "chat-messaging",
        "title": "1:1 chat",
        "difficulty": "medium",
        "sort_order": 50,
        "concept_keys": ["apis", "data", "queues", "availability"],
        "prompt": (
            "Design WhatsApp-like 1:1 messaging: send/receive, online presence, delivery receipts, "
            "and history sync when coming online."
        ),
        "constraints": "1B users · 50B messages/day · multi-device · messages must not be lost",
        "rubric_hints": {"watch": ["websocket vs polling", "inbox storage", "ordering", "offline queue"]},
        "reference_design": (
            "Connection layer (WebSocket gateways) + message service. Persist first, then push. "
            "Per-conversation ordered IDs. Offline: store undelivered; sync on reconnect. "
            "Shard by conversation or user. Receipts as separate events. Presence in ephemeral store with TTL."
        ),
    },
    {
        "slug": "video-streaming",
        "title": "Video streaming",
        "difficulty": "hard",
        "sort_order": 60,
        "concept_keys": ["caching", "queues", "availability", "estimation"],
        "prompt": (
            "Design a Netflix-like on-demand video system: upload, encode, store, and stream "
            "adaptively to global viewers."
        ),
        "constraints": "Global CDN · 4K adaptive bitrate · spike launches · storage petabyte-scale",
        "rubric_hints": {"watch": ["encoding pipeline", "CDN", "manifest", "DRM optional"]},
        "reference_design": (
            "Upload to object storage → encoding queue → ladder of renditions + HLS/DASH manifests. "
            "CDN for segments; origin shield. Metadata DB for catalog. Estimate: bitrate × hours × users "
            "for egress. Hot titles pre-warmed at edge."
        ),
    },
    {
        "slug": "ride-sharing",
        "title": "Ride matching",
        "difficulty": "hard",
        "sort_order": 70,
        "concept_keys": ["apis", "data", "sharding", "estimation"],
        "prompt": (
            "Design Uber-like dispatch: riders request trips, drivers share location, system matches "
            "nearby drivers quickly and updates ETA."
        ),
        "constraints": "City-scale · location updates every few seconds · match < 5s · surge pricing later",
        "rubric_hints": {"watch": ["geo index", "match service", "partition by city"]},
        "reference_design": (
            "Partition by city/geo. Drivers publish location to a geo-index (geohash/quadtree in Redis "
            "or specialized store). Match service queries nearby available drivers, offers, confirms. "
            "Trip state machine in primary DB. WebSockets for live updates."
        ),
    },
    {
        "slug": "search-autocomplete",
        "title": "Search autocomplete",
        "difficulty": "medium",
        "sort_order": 80,
        "concept_keys": ["caching", "estimation", "apis"],
        "prompt": (
            "Design typeahead search: as the user types, show top query suggestions within tens of "
            "milliseconds."
        ),
        "constraints": "100k QPS peak · p99 < 50ms · personalization optional",
        "rubric_hints": {"watch": ["prefix index", "edge cache", "trie vs ES"]},
        "reference_design": (
            "Prefix index (trie or n-gram) in memory per shard; CDN/edge cache for common prefixes. "
            "Offline job ranks suggestions from query logs. Limit results; debounce client-side."
        ),
    },
    {
        "slug": "notification-system",
        "title": "Notification system",
        "difficulty": "medium",
        "sort_order": 90,
        "concept_keys": ["queues", "apis", "availability"],
        "prompt": (
            "Design a multi-channel notification system (push, email, SMS) with preferences, "
            "deduplication, and retries."
        ),
        "constraints": "10k events/sec bursts · at-least-once OK · user quiet hours",
        "rubric_hints": {"watch": ["fanout", "idempotency", "provider failover"]},
        "reference_design": (
            "Ingest API → topic queue → workers per channel. Preference service gates sends. "
            "Idempotency keys avoid duplicates. Dead-letter + retry with backoff. Templates separate "
            "from delivery."
        ),
    },
    {
        "slug": "distributed-cache",
        "title": "Distributed cache",
        "difficulty": "hard",
        "sort_order": 100,
        "concept_keys": ["caching", "sharding", "availability"],
        "prompt": (
            "Design a Redis-like distributed cache cluster used by many microservices: get/set, "
            "TTL, and survival of node loss."
        ),
        "constraints": "1M QPS · sub-ms local · multi-AZ · hot keys",
        "rubric_hints": {"watch": ["consistent hashing", "replication", "eviction"]},
        "reference_design": (
            "Consistent hashing for shards; replica per shard for HA. Client or proxy routes keys. "
            "TTL + LRU eviction. Hot-key mitigation: local cache or replicate hot keys. Explicit "
            "consistency: usually single-primary per shard."
        ),
    },
    {
        "slug": "payment-ledger",
        "title": "Payment ledger",
        "difficulty": "hard",
        "sort_order": 110,
        "concept_keys": ["data", "availability", "apis"],
        "prompt": (
            "Design an internal ledger for credits and debits: exact balances, idempotent transfers, "
            "and audit history. Money must not vanish or double."
        ),
        "constraints": "Strong consistency per account · multi-currency later · auditors read history",
        "rubric_hints": {"watch": ["double-entry", "idempotency", "serializability"]},
        "reference_design": (
            "Double-entry journal append-only; balance derived or materialized under row lock / "
            "serializable txn per account. Idempotency keys on transfer API. Shard by account_id. "
            "Never update balance without a journal line."
        ),
    },
    {
        "slug": "metrics-pipeline",
        "title": "Metrics pipeline",
        "difficulty": "hard",
        "sort_order": 120,
        "concept_keys": ["queues", "sharding", "estimation"],
        "prompt": (
            "Design a metrics ingestion pipeline: millions of time-series points per second, "
            "downsampling, and dashboard queries over recent and historical data."
        ),
        "constraints": "10M points/sec peaks · retain raw 7d · rollups 1y · query p95 < 2s",
        "rubric_hints": {"watch": ["write path", "TSDB", "cardinality"],},
        "reference_design": (
            "Agents → Kafka → writers into TSDB (or wide-column). Separate hot vs cold. "
            "Rollup jobs. Careful label cardinality. Query layer fans out to shards by metric key."
        ),
    },
]

_GRADE_SYSTEM = """You are a sharp system-design mentor. The learner submitted a design for a classic case.
Be fair but exacting. Reply ONLY with JSON:
{
  "mentor_summary": "2-4 sentences that sting and teach — what worked, what is missing",
  "dimensions": [
    {"key": "framing", "score": 1-4, "note": "short"},
    {"key": "api", "score": 1-4, "note": "short"},
    {"key": "data", "score": 1-4, "note": "short"},
    {"key": "scale", "score": 1-4, "note": "short"},
    {"key": "tradeoffs", "score": 1-4, "note": "short"},
    {"key": "communication", "score": 1-4, "note": "short"}
  ],
  "weak_concepts": ["one or two concept keys from: requirements,apis,data,caching,queues,sharding,availability,estimation"],
  "lesson": {
    "title": "short principle name",
    "body": "1 short paragraph teaching the missing idea",
    "try_this": "one concrete thing to do on the next design"
  }
}
Scores: 1=missing, 2=thin, 3=solid, 4=strong. Prefer honest 2s over polite 3s.
"""


def seed_system_design_bank(db: Session) -> dict[str, int]:
    """Upsert concepts + published problems. Safe to re-run."""
    for c in CONCEPTS:
        db.execute(
            text(
                """
                INSERT INTO qb.sd_concept (key, title, blurb, sort_order, prerequisites)
                VALUES (:key, :title, :blurb, :sort_order, :prerequisites)
                ON CONFLICT (key) DO UPDATE SET
                  title = EXCLUDED.title,
                  blurb = EXCLUDED.blurb,
                  sort_order = EXCLUDED.sort_order,
                  prerequisites = EXCLUDED.prerequisites
                """
            ),
            {
                "key": c["key"],
                "title": c["title"],
                "blurb": c["blurb"],
                "sort_order": c["sort_order"],
                "prerequisites": c["prerequisites"],
            },
        )
    n_problems = 0
    for i, p in enumerate(PROBLEMS):
        db.execute(
            text(
                """
                INSERT INTO qb.sd_problem (
                  slug, title, prompt, constraints, difficulty, concept_keys,
                  rubric_hints, reference_design, published, sort_order
                )
                VALUES (
                  :slug, :title, :prompt, :constraints, :difficulty, :concept_keys,
                  CAST(:rubric_hints AS jsonb), :reference_design, true, :sort_order
                )
                ON CONFLICT (slug) DO UPDATE SET
                  title = EXCLUDED.title,
                  prompt = EXCLUDED.prompt,
                  constraints = EXCLUDED.constraints,
                  difficulty = EXCLUDED.difficulty,
                  concept_keys = EXCLUDED.concept_keys,
                  rubric_hints = EXCLUDED.rubric_hints,
                  reference_design = EXCLUDED.reference_design,
                  published = true,
                  sort_order = EXCLUDED.sort_order
                """
            ),
            {
                "slug": p["slug"],
                "title": p["title"],
                "prompt": p["prompt"],
                "constraints": p["constraints"],
                "difficulty": p["difficulty"],
                "concept_keys": p["concept_keys"],
                "rubric_hints": json.dumps(p.get("rubric_hints") or {}),
                "reference_design": p["reference_design"],
                "sort_order": p.get("sort_order", (i + 1) * 10),
            },
        )
        n_problems += 1
    db.commit()
    return {"concepts": len(CONCEPTS), "problems": n_problems}


def ensure_bank(db: Session) -> None:
    """Idempotent: seed curated bank when empty (prod migrate does not run seed scripts)."""
    n = db.execute(text("SELECT COUNT(*) FROM qb.sd_concept")).scalar() or 0
    if int(n) == 0:
        seed_system_design_bank(db)


def _subject_filter(account_id: uuid.UUID | None, guest_id: str | None) -> tuple[str, dict[str, Any]]:
    if account_id is not None:
        return "account_id = :account_id", {"account_id": account_id}
    return "guest_id = :guest_id", {"guest_id": guest_id or ""}


def list_concepts(db: Session) -> list[dict[str, Any]]:
    ensure_bank(db)
    rows = db.execute(
        text(
            """
            SELECT key, title, blurb, sort_order, prerequisites
            FROM qb.sd_concept
            ORDER BY sort_order ASC, key ASC
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def _concept_scores_for_subject(
    db: Session, account_id: uuid.UUID | None, guest_id: str | None
) -> dict[str, list[float]]:
    """Map concept_key → list of 0-1 mastery samples from done sessions."""
    where, params = _subject_filter(account_id, guest_id)
    rows = db.execute(
        text(
            f"""
            SELECT p.concept_keys, s.scores, s.weak_concepts
            FROM qb.sd_session s
            JOIN qb.sd_problem p ON p.id = s.problem_id
            WHERE s.status = 'done' AND {where}
            ORDER BY s.updated_at DESC
            LIMIT 40
            """
        ),
        params,
    ).mappings().all()
    out: dict[str, list[float]] = {}
    for r in rows:
        scores = r["scores"] or {}
        dims = scores.get("dimensions") if isinstance(scores, dict) else None
        if isinstance(dims, list) and dims:
            vals = [float(d.get("score") or 2) for d in dims if isinstance(d, dict)]
            avg = (sum(vals) / len(vals) - 1) / 3 if vals else 0.4
        else:
            avg = 0.4
        weak = set(r["weak_concepts"] or [])
        for key in r["concept_keys"] or []:
            sample = max(0.0, min(1.0, avg - (0.25 if key in weak else 0.0)))
            out.setdefault(str(key), []).append(sample)
    return out


def build_path(
    db: Session, account_id: uuid.UUID | None, guest_id: str | None
) -> dict[str, Any]:
    concepts = list_concepts(db)
    samples = _concept_scores_for_subject(db, account_id, guest_id)
    items = []
    focus_key: str | None = None
    for c in concepts:
        key = c["key"]
        hist = samples.get(key) or []
        if not hist:
            state = "not_started"
            mastery = None
        else:
            mastery = round(sum(hist[:5]) / min(5, len(hist)), 2)
            state = "strong" if mastery >= 0.72 else "in_progress" if mastery >= 0.35 else "needs_work"
        if focus_key is None and state in ("not_started", "needs_work", "in_progress"):
            if state != "strong":
                focus_key = key
        items.append(
            {
                "key": key,
                "title": c["title"],
                "blurb": c["blurb"],
                "prerequisites": list(c["prerequisites"] or []),
                "state": state,
                "mastery": mastery,
            }
        )
    if focus_key is None and items:
        focus_key = items[0]["key"]
    focus_title = next((i["title"] for i in items if i["key"] == focus_key), "")
    return {"concepts": items, "focus_key": focus_key, "focus_title": focus_title}


def get_problem(db: Session, problem_id: uuid.UUID, *, include_reference: bool = False) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT id, slug, title, prompt, constraints, difficulty, concept_keys,
                   rubric_hints, reference_design, sort_order
            FROM qb.sd_problem
            WHERE id = :id AND published = true
            """
        ),
        {"id": problem_id},
    ).mappings().first()
    if not row:
        return None
    out = dict(row)
    out["id"] = str(out["id"])
    if not include_reference:
        out.pop("reference_design", None)
    else:
        out["reference_design"] = out.get("reference_design") or ""
    return out


def get_problem_by_slug(db: Session, slug: str) -> dict[str, Any] | None:
    row = db.execute(
        text("SELECT id FROM qb.sd_problem WHERE slug = :slug AND published = true"),
        {"slug": slug},
    ).mappings().first()
    if not row:
        return None
    return get_problem(db, row["id"], include_reference=False)


def list_problems(db: Session, *, difficulty: str | None = None) -> list[dict[str, Any]]:
    ensure_bank(db)
    clauses = ["published = true"]
    params: dict[str, Any] = {}
    if difficulty in ("easy", "medium", "hard"):
        clauses.append("difficulty = :difficulty")
        params["difficulty"] = difficulty
    rows = db.execute(
        text(
            f"""
            SELECT id, slug, title, prompt, constraints, difficulty, concept_keys, sort_order
            FROM qb.sd_problem
            WHERE {' AND '.join(clauses)}
            ORDER BY sort_order ASC, title ASC
            """
        ),
        params,
    ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        out.append(d)
    return out


def recommend_problem(
    db: Session, account_id: uuid.UUID | None, guest_id: str | None
) -> dict[str, Any] | None:
    path = build_path(db, account_id, guest_id)
    focus = path.get("focus_key")
    where, params = _subject_filter(account_id, guest_id)
    attempted = {
        str(r["problem_id"])
        for r in db.execute(
            text(
                f"""
                SELECT DISTINCT problem_id FROM qb.sd_session
                WHERE status = 'done' AND {where}
                """
            ),
            params,
        ).mappings().all()
    }
    problems = list_problems(db)
    # Prefer unattempted problems tagged with focus concept.
    ranked: list[dict[str, Any]] = []
    for p in problems:
        keys = set(p.get("concept_keys") or [])
        score = 0
        if focus and focus in keys:
            score += 10
        if str(p["id"]) not in attempted:
            score += 5
        else:
            score -= 3
        # Easier first when not started on focus
        score += {"easy": 2, "medium": 1, "hard": 0}.get(p.get("difficulty") or "", 0)
        ranked.append((score, p))
    ranked.sort(key=lambda x: (-x[0], x[1].get("sort_order") or 0))
    return ranked[0][1] if ranked else None


def active_session(
    db: Session, account_id: uuid.UUID | None, guest_id: str | None
) -> dict[str, Any] | None:
    where, params = _subject_filter(account_id, guest_id)
    row = db.execute(
        text(
            f"""
            SELECT id FROM qb.sd_session
            WHERE status = 'active' AND {where}
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ),
        params,
    ).mappings().first()
    if not row:
        return None
    return get_session(db, row["id"], account_id, guest_id)


def start_session(
    db: Session,
    problem_id: uuid.UUID,
    account_id: uuid.UUID | None,
    guest_id: str | None,
) -> dict[str, Any]:
    if account_id is None and not guest_id:
        raise ValueError("subject required")
    problem = get_problem(db, problem_id)
    if not problem:
        raise LookupError("problem not found")
    # Close other active sessions for this subject.
    where, params = _subject_filter(account_id, guest_id)
    db.execute(
        text(f"UPDATE qb.sd_session SET status = 'done', updated_at = now() WHERE status = 'active' AND {where}"),
        params,
    )
    sid = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO qb.sd_session (id, problem_id, account_id, guest_id, status, design)
            VALUES (:id, :problem_id, :account_id, :guest_id, 'active', '{}'::jsonb)
            """
        ),
        {
            "id": sid,
            "problem_id": problem_id,
            "account_id": account_id,
            "guest_id": guest_id,
        },
    )
    db.commit()
    return get_session(db, sid, account_id, guest_id) or {"id": str(sid)}


def get_session(
    db: Session,
    session_id: uuid.UUID,
    account_id: uuid.UUID | None,
    guest_id: str | None,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT s.id, s.problem_id, s.account_id, s.guest_id, s.status, s.design,
                   s.scores, s.feedback, s.weak_concepts, s.lesson, s.recommended_next_id,
                   s.created_at, s.updated_at
            FROM qb.sd_session s
            WHERE s.id = :id
            """
        ),
        {"id": session_id},
    ).mappings().first()
    if not row:
        return None
    if account_id is not None:
        if row["account_id"] != account_id:
            return None
    elif row["guest_id"] != guest_id:
        return None
    problem = get_problem(
        db, row["problem_id"], include_reference=(row["status"] == "done")
    )
    out = {
        "id": str(row["id"]),
        "problem_id": str(row["problem_id"]),
        "status": row["status"],
        "design": row["design"] or {},
        "scores": row["scores"] or {},
        "feedback": row["feedback"] or {},
        "weak_concepts": list(row["weak_concepts"] or []),
        "lesson": row["lesson"] or {},
        "recommended_next_id": str(row["recommended_next_id"]) if row["recommended_next_id"] else None,
        "problem": problem,
        "building_blocks": BUILDING_BLOCKS,
    }
    if row["status"] == "done" and problem:
        out["reference_design"] = problem.get("reference_design") or ""
    return out


def save_design(
    db: Session,
    session_id: uuid.UUID,
    account_id: uuid.UUID | None,
    guest_id: str | None,
    design: dict[str, Any],
) -> dict[str, Any]:
    sess = get_session(db, session_id, account_id, guest_id)
    if not sess:
        raise LookupError("session not found")
    if sess["status"] != "active":
        raise ValueError("session already finished")
    clean = {
        "requirements": str(design.get("requirements") or "")[:12000],
        "apis": str(design.get("apis") or "")[:12000],
        "data": str(design.get("data") or "")[:12000],
        "scale": str(design.get("scale") or "")[:12000],
        "blocks": [str(b) for b in (design.get("blocks") or []) if str(b)][:24],
    }
    db.execute(
        text(
            """
            UPDATE qb.sd_session
            SET design = CAST(:design AS jsonb), updated_at = now()
            WHERE id = :id
            """
        ),
        {"id": session_id, "design": json.dumps(clean)},
    )
    db.commit()
    return get_session(db, session_id, account_id, guest_id) or sess


def _clamp_score(v: Any) -> int:
    try:
        return max(1, min(4, int(round(float(v)))))
    except (TypeError, ValueError):
        return 2


def _heuristic_grade(design: dict[str, Any], concept_keys: list[str]) -> dict[str, Any]:
    text_blob = " ".join(
        str(design.get(k) or "") for k in ("requirements", "apis", "data", "scale")
    )
    words = len(re.findall(r"\w+", text_blob))
    blocks = design.get("blocks") or []
    base = 2
    if words > 80:
        base = 3
    if words < 25:
        base = 1
    dims = []
    for key in ("framing", "api", "data", "scale", "tradeoffs", "communication"):
        score = base
        if key == "api" and ("api" in text_blob.lower() or "endpoint" in text_blob.lower()):
            score = min(4, score + 1)
        if key == "data" and any(w in text_blob.lower() for w in ("db", "database", "sql", "store")):
            score = min(4, score + 1)
        if key == "scale" and any(w in text_blob.lower() for w in ("cache", "shard", "qps", "cdn", "queue")):
            score = min(4, score + 1)
        if key == "framing" and words > 40:
            score = min(4, max(score, 2))
        if blocks and key == "communication":
            score = min(4, score + 1)
        dims.append({"key": key, "score": score, "note": "Heuristic score — model unavailable."})
    weak = list(concept_keys[:1]) or ["requirements"]
    return {
        "mentor_summary": (
            "You sketched a direction, but the interesting constraints are still thin. "
            "Name the hot path, the data ownership, and one failure mode before drawing more boxes."
        ),
        "dimensions": dims,
        "weak_concepts": weak,
        "lesson": {
            "title": "Start from the hot path",
            "body": (
                "Great designs begin with the request that happens most often and the data it "
                "must touch. Write that path end-to-end before optimizing side features."
            ),
            "try_this": "On the next case, write the single most common request as a numbered sequence of hops.",
        },
    }


async def submit_and_grade(
    db: Session,
    session_id: uuid.UUID,
    account_id: uuid.UUID | None,
    guest_id: str | None,
    design: dict[str, Any],
) -> dict[str, Any]:
    sess = save_design(db, session_id, account_id, guest_id, design)
    problem = sess.get("problem") or {}
    concept_keys = list(problem.get("concept_keys") or [])
    design_clean = sess.get("design") or {}

    from app.services.chunk_map_cache import content_hash_key
    from app.services.generation_cache import get as cache_get, put as cache_put

    design_json = json.dumps(design_clean, sort_keys=True, default=str)
    grade_key = content_hash_key(
        "sd_grade",
        str(sess.get("problem_id") or ""),
        design_json,
    )
    cached = cache_get(db, kind="sd_grade", cache_key=grade_key)
    graded: dict[str, Any]
    if isinstance(cached, dict) and cached.get("mentor_summary") and cached.get("lesson"):
        graded = cached
    else:
        user = (
            f"Case: {problem.get('title')}\n"
            f"Prompt: {problem.get('prompt')}\n"
            f"Constraints: {problem.get('constraints')}\n"
            f"Concept keys: {', '.join(concept_keys)}\n\n"
            f"Learner's design JSON:\n{design_json}"
        )
        try:
            raw = await complete_chat(
                [
                    {"role": "system", "content": _GRADE_SYSTEM},
                    {"role": "user", "content": user},
                ],
                db,
                log_tag="sd_grade",
            )
            data = extract_json_obj(raw) or {}
            dims_in = data.get("dimensions") or []
            dims = []
            for key in ("framing", "api", "data", "scale", "tradeoffs", "communication"):
                found = next((d for d in dims_in if isinstance(d, dict) and d.get("key") == key), None)
                dims.append(
                    {
                        "key": key,
                        "score": _clamp_score((found or {}).get("score")),
                        "note": str((found or {}).get("note") or "")[:280],
                    }
                )
            # Fill gaps from heuristic so lesson shape always matches coding teach-gap.
            fallback = _heuristic_grade(design_clean, concept_keys)
            weak = [str(w) for w in (data.get("weak_concepts") or []) if str(w)][:3]
            if not weak:
                weak = fallback["weak_concepts"]
            lesson = data.get("lesson") if isinstance(data.get("lesson"), dict) else {}
            graded = {
                "mentor_summary": str(data.get("mentor_summary") or "").strip()
                or fallback["mentor_summary"],
                "dimensions": dims,
                "weak_concepts": weak,
                "lesson": {
                    "title": str(lesson.get("title") or fallback["lesson"]["title"])[:120],
                    "body": str(lesson.get("body") or fallback["lesson"]["body"])[:2000],
                    "try_this": str(lesson.get("try_this") or fallback["lesson"]["try_this"])[:400],
                },
            }
            cache_put(db, kind="sd_grade", cache_key=grade_key, value=graded)
        except Exception:
            # Best-effort LLM grade — heuristic keeps the mastery loop alive.
            graded = _heuristic_grade(design_clean, concept_keys)

    next_id = _pick_next_problem_id(db, concept_keys=graded["weak_concepts"], exclude=uuid.UUID(sess["problem_id"]))
    db.execute(
        text(
            """
            UPDATE qb.sd_session SET
              status = 'done',
              scores = CAST(:scores AS jsonb),
              feedback = CAST(:feedback AS jsonb),
              weak_concepts = :weak,
              lesson = CAST(:lesson AS jsonb),
              recommended_next_id = :next_id,
              updated_at = now()
            WHERE id = :id
            """
        ),
        {
            "id": session_id,
            "scores": json.dumps({"dimensions": graded["dimensions"]}),
            "feedback": json.dumps({"mentor_summary": graded["mentor_summary"]}),
            "weak": graded["weak_concepts"],
            "lesson": json.dumps(graded["lesson"]),
            "next_id": next_id,
        },
    )
    db.commit()
    return get_session(db, session_id, account_id, guest_id) or sess


def _pick_next_problem_id(
    db: Session, *, concept_keys: list[str], exclude: uuid.UUID
) -> uuid.UUID | None:
    problems = list_problems(db)
    focus = set(concept_keys)
    best: tuple[int, dict[str, Any]] | None = None
    for p in problems:
        if str(p["id"]) == str(exclude):
            continue
        keys = set(p.get("concept_keys") or [])
        overlap = len(focus & keys)
        score = overlap * 10 + {"easy": 1, "medium": 2, "hard": 3}.get(p.get("difficulty") or "", 0)
        if best is None or score > best[0]:
            best = (score, p)
    if not best:
        return None
    return uuid.UUID(best[1]["id"])
