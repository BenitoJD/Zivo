"""SEO cook pipeline — candidates, gates, write, MCQ, publish."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.repositories import seo as seo_repo
from app.services.content_worthiness import evaluate_worthiness
from app.services.seo_dedupe import (
    embed_title_lede,
    topic_fingerprint,
    unique_slug,
)
from app.services.seo_gate import evaluate_dedupe, evaluate_usefulness
from app.services.seo_mcq import attach_or_generate_mcqs
from app.services.seo_pii import scrub_pii
from app.services.seo_writer import write_article

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class Candidate:
    source_kind: str
    source_key: str
    stream: str
    text: str
    title_hint: str = ""
    angle_prompt: str = ""
    source_ref: dict[str, Any] | None = None
    source_document_id: uuid.UUID | None = None
    filename: str = ""
    format_override: str | None = None


def _today_ist() -> date:
    return datetime.now(_IST).date()


def _skip(db: Session, cand: Candidate, reason: str) -> dict[str, Any]:
    seo_repo.record_attempt(
        db,
        source_kind=cand.source_kind,
        source_key=cand.source_key,
        outcome="skipped",
        reason=reason,
    )
    db.commit()
    logger.info("seo skip %s/%s: %s", cand.source_kind, cand.source_key, reason)
    return {"skipped": True, "reason": reason, "source_key": cand.source_key}


def collect_candidates(db: Session, *, batch_size: int = 5) -> list[Candidate]:
    """Ordered: newspaper cook pages → uploads → (SD only via ensure_sd)."""
    out: list[Candidate] = []

    for row in seo_repo.list_newspaper_cook_candidates(db, limit=batch_size):
        page_text = (row.get("text") or "").strip()
        if not evaluate_worthiness(page_text=page_text, newspaper=True).worthy:
            # Mark so we don't keep re-checking ads
            key = f"{row['document_id']}:{row['page_start']}"
            seo_repo.record_attempt(
                db,
                source_kind="newspaper",
                source_key=key,
                outcome="skipped",
                reason="not_cook_verdict",
            )
            continue
        out.append(
            Candidate(
                source_kind="newspaper",
                source_key=f"{row['document_id']}:{row['page_start']}",
                stream="general",
                text=page_text,
                title_hint="",
                source_ref={
                    "document_id": str(row["document_id"]),
                    "edition_id": str(row["edition_id"]),
                    "page_start": row["page_start"],
                    "paper_slug": row.get("paper_slug"),
                },
                source_document_id=uuid.UUID(str(row["document_id"])),
            )
        )
        if len(out) >= batch_size:
            break

    if len(out) < batch_size:
        for row in seo_repo.list_upload_candidates(db, limit=batch_size):
            out.append(
                Candidate(
                    source_kind="upload",
                    source_key=str(row["document_id"]),
                    stream="general",
                    text=(row.get("text") or "").strip(),
                    filename=str(row.get("filename") or ""),
                    source_ref={"document_id": str(row["document_id"])},
                    source_document_id=uuid.UUID(str(row["document_id"])),
                )
            )
            if len(out) >= batch_size:
                break

    db.commit()
    return out


def candidate_from_sd_problem(row: dict[str, Any]) -> Candidate:
    parts = [
        str(row.get("title") or ""),
        str(row.get("prompt") or ""),
        str(row.get("constraints") or ""),
        str(row.get("reference_design") or "")[:4000],
    ]
    return Candidate(
        source_kind="sd_bank",
        source_key=str(row["slug"]),
        stream="system_design",
        text="\n\n".join(p for p in parts if p).strip(),
        title_hint=str(row.get("title") or ""),
        angle_prompt="Teach the system design idea. No 'from our bank' language.",
        source_ref={"sd_problem_id": str(row["id"]), "sd_slug": row["slug"]},
        format_override="explainer",
    )


def candidate_from_topic(row: dict[str, Any]) -> Candidate:
    return Candidate(
        source_kind="topic_queue",
        source_key=str(row["topic_key"]),
        stream=str(row.get("stream") or "system_design"),
        text=(
            f"Topic: {row.get('title_hint')}\n"
            f"Angle: {row.get('angle_prompt')}\n"
            "Write a self-contained teaching article from first principles."
        ),
        title_hint=str(row.get("title_hint") or ""),
        angle_prompt=str(row.get("angle_prompt") or ""),
        source_ref={"queue_id": str(row["id"]), "topic_key": row["topic_key"]},
        format_override="explainer",
    )


def cook_one(db: Session, cand: Candidate) -> dict[str, Any]:
    """Run all gates + write + MCQ + publish for one candidate."""
    settings = seo_repo.get_settings(db)
    if not settings.get("cook_enabled"):
        return {"skipped": True, "reason": "cook_disabled"}

    today = _today_ist()
    soft_max = int(settings.get("soft_max_per_day") or 20)
    if seo_repo.count_published_on_day(db, today) >= soft_max:
        return {"skipped": True, "reason": "soft_max"}

    if seo_repo.attempt_exists(db, cand.source_kind, cand.source_key):
        return {"skipped": True, "reason": "already_attempted"}

    usefulness = evaluate_usefulness(cand.text, filename=cand.filename)
    if not usefulness.useful:
        return _skip(db, cand, f"triage:{usefulness.reason}")

    scrubbed = scrub_pii(cand.text)
    article = write_article(
        db,
        source_text=scrubbed,
        stream=cand.stream,
        title_hint=cand.title_hint,
        angle_prompt=cand.angle_prompt,
        format_override=cand.format_override,
    )
    if not article:
        return _skip(db, cand, "write_failed")

    fp = topic_fingerprint(
        cand.stream,
        article.get("topic_fingerprint_hint") or article["title"],
    )
    # SD bank uses stable fingerprint so we don't re-cook same problem
    if cand.source_kind == "sd_bank":
        fp = f"sd:{cand.source_key}"
    elif cand.source_kind == "topic_queue":
        fp = f"topic:{cand.source_key}"

    dedupe = evaluate_dedupe(
        db,
        fingerprint=fp,
        title=article["title"],
        lede=article["lede"],
    )
    if not dedupe.ok:
        return _skip(db, cand, f"dedupe:{dedupe.reason}")

    slug = unique_slug(db, article["title"])
    embedding = embed_title_lede(article["title"], article["lede"])
    author = seo_repo.next_author_name(db)

    post_id = seo_repo.insert_post(
        db,
        slug=slug,
        title=article["title"],
        lede=article["lede"],
        body_md=article["body_md"],
        format=article["format"],
        stream=article["stream"],
        author_name=author,
        topic_fingerprint=fp,
        embedding=embedding,
        source_kind=cand.source_kind,
        source_ref=cand.source_ref or {},
        faq_jsonld=article.get("faq_jsonld") or [],
        cta_kind=article.get("cta_kind") or "practice",
        status="published",
    )

    try:
        attach_or_generate_mcqs(
            db,
            post_id=post_id,
            slug=slug,
            body_md=article["body_md"],
            source_document_id=cand.source_document_id,
        )
    except Exception:
        logger.exception("seo mcq attach failed post=%s", post_id)

    if cand.source_kind == "topic_queue" and cand.source_ref.get("queue_id"):
        try:
            seo_repo.mark_topic_used(db, uuid.UUID(str(cand.source_ref["queue_id"])))
        except (ValueError, TypeError):
            pass

    seo_repo.record_attempt(
        db,
        source_kind=cand.source_kind,
        source_key=cand.source_key,
        outcome="published",
        reason="ok",
        post_id=post_id,
    )
    db.commit()
    logger.info("seo published %s slug=%s stream=%s", post_id, slug, article["stream"])
    return {
        "published": True,
        "post_id": str(post_id),
        "slug": slug,
        "stream": article["stream"],
    }


def cook_batch(db: Session, *, limit: int = 3) -> dict[str, Any]:
    settings = seo_repo.get_settings(db)
    if not settings.get("cook_enabled"):
        return {"ok": True, "skipped": "cook_disabled", "results": []}

    today = _today_ist()
    soft_max = int(settings.get("soft_max_per_day") or 20)
    remaining = soft_max - seo_repo.count_published_on_day(db, today)
    if remaining <= 0:
        return {"ok": True, "skipped": "soft_max", "results": []}

    n = min(limit, remaining, 5)
    results = []
    for cand in collect_candidates(db, batch_size=n):
        if seo_repo.count_published_on_day(db, today) >= soft_max:
            break
        results.append(cook_one(db, cand))
    return {"ok": True, "results": results}


def ensure_sd_daily(db: Session) -> dict[str, Any]:
    """Every IST calendar day ≥1 published system_design post."""
    settings = seo_repo.get_settings(db)
    if not settings.get("cook_enabled"):
        return {"ok": True, "skipped": "cook_disabled"}

    today = _today_ist()
    if seo_repo.count_sd_published_on_day(db, today) >= 1:
        return {"ok": True, "skipped": "already_have_sd"}

    soft_max = int(settings.get("soft_max_per_day") or 20)
    if seo_repo.count_published_on_day(db, today) >= soft_max:
        return {"ok": True, "skipped": "soft_max"}

    row = seo_repo.pick_unused_sd_problem(db)
    if row:
        return cook_one(db, candidate_from_sd_problem(row))

    topic = seo_repo.pick_topic_queue(db, stream="system_design")
    if topic:
        return cook_one(db, candidate_from_topic(topic))

    return {"ok": False, "skipped": "no_sd_candidates"}
