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
from app.services.seo_gate import (
    evaluate_dedupe,
    evaluate_publish_cap,
    evaluate_usefulness,
)
from app.services.seo_mcq import attach_edition_mcqs, attach_or_generate_mcqs
from app.services.seo_pii import scrub_pii
from app.services.seo_writer import write_article, write_edition_digest

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
        if not evaluate_worthiness(page_text=page_text, newspaper=True, db=db).worthy:
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
    cap = evaluate_publish_cap(
        published_today=seo_repo.count_published_on_day(db, today),
        soft_max_per_day=settings.get("soft_max_per_day"),
    )
    if not cap.allow:
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
    cap = evaluate_publish_cap(
        published_today=seo_repo.count_published_on_day(db, today),
        soft_max_per_day=settings.get("soft_max_per_day"),
    )
    if not cap.allow:
        return {"ok": True, "skipped": "soft_max", "results": []}

    n = min(limit, cap.remaining, 5)
    results = []
    for cand in collect_candidates(db, batch_size=n):
        again = evaluate_publish_cap(
            published_today=seo_repo.count_published_on_day(db, today),
            soft_max_per_day=settings.get("soft_max_per_day"),
        )
        if not again.allow:
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

    cap = evaluate_publish_cap(
        published_today=seo_repo.count_published_on_day(db, today),
        soft_max_per_day=settings.get("soft_max_per_day"),
    )
    if not cap.allow:
        return {"ok": True, "skipped": "soft_max"}

    row = seo_repo.pick_unused_sd_problem(db)
    if row:
        return cook_one(db, candidate_from_sd_problem(row))

    topic = seo_repo.pick_topic_queue(db, stream="system_design")
    if topic:
        return cook_one(db, candidate_from_topic(topic))

    return {"ok": False, "skipped": "no_sd_candidates"}


def _edition_source_key(paper_slug: str, edition_date: date) -> str:
    return f"{paper_slug}:{edition_date.isoformat()}"


def _edition_fingerprint(paper_slug: str, edition_date: date) -> str:
    return f"newspaper:{paper_slug}:{edition_date.isoformat()}"


def cook_edition_digest(
    db: Session, edition_id: uuid.UUID, *, force: bool = False
) -> dict[str, Any]:
    """Cook one edition digest when the edition is ready."""
    from app.repositories import newspaper as newspaper_repo
    from app.services.newspaper import aggregate_worthy_newspaper_text

    settings = seo_repo.get_settings(db)
    if not settings.get("cook_enabled"):
        return {"skipped": True, "reason": "cook_disabled"}

    ed = newspaper_repo.get_edition(db, edition_id)
    if not ed:
        return {"skipped": True, "reason": "edition_not_found"}
    if ed.get("status") != "ready":
        return {"skipped": True, "reason": "edition_not_ready"}
    if ed.get("blog_status") == "published" and ed.get("blog_post_id") and not force:
        return {"skipped": True, "reason": "already_published"}

    paper_slug = str(ed["paper_slug"])
    edition_date = ed["edition_date"]
    source_key = _edition_source_key(paper_slug, edition_date)
    fp = _edition_fingerprint(paper_slug, edition_date)
    existing_post_id = (
        uuid.UUID(str(ed["blog_post_id"])) if ed.get("blog_post_id") else None
    )

    linked = _sync_edition_blog_link(db, edition_id=edition_id, fingerprint=fp)
    if linked and not force:
        return linked

    if not force and not seo_repo.edition_digest_may_enqueue(
        db, "newspaper_edition", source_key
    ):
        return {"skipped": True, "reason": "already_attempted"}

    if not force:
        newspaper_repo.reset_edition_blog_for_retry(db, edition_id)

    doc_id = ed.get("document_id")
    if not doc_id:
        return _skip_edition(db, edition_id, source_key, "no_document")

    source_text = aggregate_worthy_newspaper_text(db, uuid.UUID(str(doc_id)))
    if len(source_text.strip()) < 400:
        return _skip_edition(db, edition_id, source_key, "insufficient_text")

    scrubbed = scrub_pii(source_text)
    title_hint = f"{ed.get('paper_title') or paper_slug} · {edition_date.isoformat()}"
    article = write_edition_digest(
        db,
        source_text=scrubbed,
        edition_date=edition_date.isoformat(),
        title_hint=title_hint,
    )
    if not article:
        return _skip_edition(db, edition_id, source_key, "write_failed")

    fp = _edition_fingerprint(paper_slug, edition_date)
    dedupe = evaluate_dedupe(
        db,
        fingerprint=fp,
        title=article["title"],
        lede=article["lede"],
    )
    if not dedupe.ok and not (force and existing_post_id):
        if dedupe.reason == "fingerprint_taken":
            linked = _link_existing_edition_post(
                db,
                edition_id=edition_id,
                fingerprint=fp,
                source_key=source_key,
            )
            if linked:
                return linked
        return _skip_edition(db, edition_id, source_key, f"dedupe:{dedupe.reason}")

    embedding = embed_title_lede(article["title"], article["lede"])

    if force and existing_post_id:
        post_id = existing_post_id
        existing = seo_repo.get_post_by_id(db, post_id, include_internal=True) or {}
        slug = str(existing.get("slug") or f"{paper_slug}-{edition_date.isoformat()}")
        seo_repo.update_post_content(
            db,
            post_id,
            title=article["title"],
            lede=article["lede"],
            body_md=article["body_md"],
            embedding=embedding,
        )
    else:
        slug = unique_slug(db, f"{paper_slug}-{edition_date.isoformat()}")
        author = seo_repo.next_author_name(db)
        newspaper_repo.set_edition_blog_status(db, edition_id, status="pending")
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
            source_kind="newspaper_edition",
            source_ref={
                "edition_id": str(edition_id),
                "paper_slug": paper_slug,
                "edition_date": edition_date.isoformat(),
            },
            faq_jsonld=[],
            cta_kind="practice",
            status="published",
        )

    try:
        attached = attach_edition_mcqs(
            db,
            post_id=post_id,
            document_id=uuid.UUID(str(doc_id)),
        )
        if len(attached) < 3:
            attach_or_generate_mcqs(
                db,
                post_id=post_id,
                slug=slug,
                body_md=article["body_md"],
                source_document_id=uuid.UUID(str(doc_id)),
                target_count=4,
            )
    except Exception:
        logger.exception("edition digest mcq attach failed post=%s", post_id)

    newspaper_repo.link_edition_blog(
        db,
        edition_id=edition_id,
        post_id=post_id,
        status="published",
    )
    seo_repo.record_attempt(
        db,
        source_kind="newspaper_edition",
        source_key=source_key,
        outcome="published",
        reason="recooked" if force else "ok",
        post_id=post_id,
    )
    db.commit()
    logger.info(
        "edition digest %s edition=%s slug=%s",
        "recooked" if force else "published",
        edition_id,
        slug,
    )
    return {
        "published": True,
        "recooked": force,
        "post_id": str(post_id),
        "slug": slug,
        "edition_id": str(edition_id),
    }


def _link_existing_edition_post(
    db: Session,
    *,
    edition_id: uuid.UUID,
    fingerprint: str,
    source_key: str,
) -> dict[str, Any] | None:
    """Attach edition to an already-published digest for this fingerprint."""
    from app.repositories import newspaper as newspaper_repo

    existing = seo_repo.get_published_post_by_fingerprint(db, fingerprint)
    if not existing:
        return None
    post_id = uuid.UUID(str(existing["id"]))
    newspaper_repo.link_edition_blog(
        db,
        edition_id=edition_id,
        post_id=post_id,
        status="published",
    )
    seo_repo.record_attempt(
        db,
        source_kind="newspaper_edition",
        source_key=source_key,
        outcome="published",
        reason="linked_existing",
        post_id=post_id,
    )
    db.commit()
    logger.info(
        "edition digest linked existing edition=%s post=%s",
        edition_id,
        post_id,
    )
    return {
        "published": True,
        "linked_existing": True,
        "post_id": str(post_id),
        "edition_id": str(edition_id),
    }


def _sync_edition_blog_link(
    db: Session,
    *,
    edition_id: uuid.UUID,
    fingerprint: str,
) -> dict[str, Any] | None:
    """Repair or short-circuit when a published post is already linked."""
    from app.repositories import newspaper as newspaper_repo

    ed = newspaper_repo.get_edition(db, edition_id)
    if not ed:
        return None
    post_id = ed.get("blog_post_id")
    if post_id:
        post = seo_repo.get_post_by_id(db, uuid.UUID(str(post_id)), include_internal=True)
        if post and post.get("status") == "published":
            if ed.get("blog_status") != "published":
                newspaper_repo.link_edition_blog(
                    db,
                    edition_id=edition_id,
                    post_id=uuid.UUID(str(post_id)),
                    status="published",
                )
                db.commit()
            return {"skipped": True, "reason": "already_linked"}

    existing = seo_repo.get_published_post_by_fingerprint(db, fingerprint)
    if not existing:
        return None
    source_key = _edition_source_key(str(ed["paper_slug"]), ed["edition_date"])
    return _link_existing_edition_post(
        db,
        edition_id=edition_id,
        fingerprint=fingerprint,
        source_key=source_key,
    )


def _skip_edition(
    db: Session,
    edition_id: uuid.UUID,
    source_key: str,
    reason: str,
) -> dict[str, Any]:
    from app.repositories import newspaper as newspaper_repo

    ed = newspaper_repo.get_edition(db, edition_id)
    if ed and ed.get("blog_post_id"):
        post = seo_repo.get_post_by_id(
            db, uuid.UUID(str(ed["blog_post_id"])), include_internal=True
        )
        if post and post.get("status") == "published":
            newspaper_repo.link_edition_blog(
                db,
                edition_id=edition_id,
                post_id=uuid.UUID(str(ed["blog_post_id"])),
                status="published",
            )
            seo_repo.record_attempt(
                db,
                source_kind="newspaper_edition",
                source_key=source_key,
                outcome="published",
                reason="linked_existing",
                post_id=uuid.UUID(str(ed["blog_post_id"])),
            )
            db.commit()
            logger.info(
                "edition digest kept published link %s (skip reason=%s)",
                source_key,
                reason,
            )
            return {
                "published": True,
                "linked_existing": True,
                "reason": reason,
                "source_key": source_key,
            }

    seo_repo.record_attempt(
        db,
        source_kind="newspaper_edition",
        source_key=source_key,
        outcome="skipped",
        reason=reason,
    )
    blog_status = (
        "failed"
        if reason in seo_repo.RETRYABLE_EDITION_DIGEST_REASONS
        else "skipped"
    )
    if not (ed and ed.get("blog_post_id")):
        newspaper_repo.set_edition_blog_status(db, edition_id, status=blog_status)
    db.commit()
    logger.info("edition digest skip %s: %s", source_key, reason)
    return {"skipped": True, "reason": reason, "source_key": source_key}
