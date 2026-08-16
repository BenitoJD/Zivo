"""SEO cook pipeline — candidates, gates, write, MCQ, publish."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.engine_runtime import apply, choose, pick
from app.repositories import seo as seo_repo
from app.services.seo_dedupe import (
    embed_title_lede,
    topic_fingerprint,
    unique_slug,
)
from app.services.seo_gate import (
    digest_skip_reason,
    evaluate_dedupe,
    evaluate_digest_source_ready,
    evaluate_edition_blog_link,
    evaluate_edition_digest_preflight,
    evaluate_edition_digest_skip_status,
    evaluate_edition_skip_link,
    evaluate_newspaper_seo_candidate,
    evaluate_publish_cap,
    evaluate_seo_cook_enabled,
    evaluate_seo_mcq_attach_ready,
    evaluate_usefulness,
    plan_sd_daily_cook,
    plan_seo_candidate_schedule,
    plan_seo_cook_tick,
    plan_seo_fingerprint,
    plan_seo_mcq_attach_defaults,
    plan_seo_sd_source_chars,
    seo_candidate_slots_remaining,
    should_bypass_digest_dedupe,
    SEO_COOK_TICK_DEFAULT,
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


def collect_candidates(db: Session, *, batch_size: int | None = None) -> list[Candidate]:
    """Ordered: newspaper cook pages → uploads → (SD only via ensure_sd)."""
    schedule = plan_seo_candidate_schedule(batch_size=batch_size)
    out: list[Candidate] = []
    newspaper_rows = list(seo_repo.list_newspaper_cook_candidates(db, limit=schedule.batch_size))
    _ingest_newspaper_rows(db, newspaper_rows, out, schedule.batch_size)
    upload_rows = list(seo_repo.list_upload_candidates(db, limit=schedule.batch_size))
    _ingest_upload_rows(db, upload_rows, out, schedule.batch_size)
    db.commit()
    return out


def _ingest_newspaper_rows(
    db: Session, rows: list[Any], out: list[Candidate], batch_size: int
) -> None:
    pick(
        not rows,
        lambda: None,
        lambda: _ingest_one_newspaper(db, rows, out, batch_size),
    )


def _ingest_one_newspaper(
    db: Session, rows: list[Any], out: list[Candidate], batch_size: int
) -> None:
    row = rows.pop(0)
    page_text = (row.get("text") or "").strip()
    pick(
        not evaluate_newspaper_seo_candidate(page_text, db=db),
        lambda: _record_newspaper_not_cook(db, row)
        or _ingest_newspaper_rows(db, rows, out, batch_size),
        lambda: _accept_newspaper(db, row, page_text, out, batch_size, rows),
    )


def _record_newspaper_not_cook(db: Session, row: Any) -> None:
    key = f"{row['document_id']}:{row['page_start']}"
    seo_repo.record_attempt(
        db,
        source_kind="newspaper",
        source_key=key,
        outcome="skipped",
        reason="not_cook_verdict",
    )


def _accept_newspaper(
    db: Session,
    row: Any,
    page_text: str,
    out: list[Candidate],
    batch_size: int,
    rows: list[Any],
) -> None:
    out.append(_newspaper_candidate(row, page_text))
    pick(
        seo_candidate_slots_remaining(accepted=len(out), batch_size=batch_size) <= 0,
        lambda: None,
        lambda: _ingest_newspaper_rows(db, rows, out, batch_size),
    )


def _newspaper_candidate(row: Any, page_text: str) -> Candidate:
    return Candidate(
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


def _ingest_upload_rows(
    db: Session, rows: list[Any], out: list[Candidate], batch_size: int
) -> None:
    pick(
        not rows
        or seo_candidate_slots_remaining(accepted=len(out), batch_size=batch_size) <= 0,
        lambda: None,
        lambda: _ingest_one_upload(db, rows, out, batch_size),
    )


def _ingest_one_upload(
    db: Session, rows: list[Any], out: list[Candidate], batch_size: int
) -> None:
    row = rows.pop(0)
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
    _ingest_upload_rows(db, rows, out, batch_size)


def candidate_from_sd_problem(row: dict[str, Any]) -> Candidate:
    parts = [
        str(row.get("title") or ""),
        str(row.get("prompt") or ""),
        str(row.get("constraints") or ""),
        str(row.get("reference_design") or "")[: plan_seo_sd_source_chars()],
    ]
    return Candidate(
        source_kind="sd_bank",
        source_key=str(row["slug"]),
        stream="system_design",
        text="\n\n".join(filter(None, parts)).strip(),
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
    return pick(
        not evaluate_seo_cook_enabled(settings),
        lambda: {"skipped": True, "reason": "cook_disabled"},
        lambda: _cook_one_cap(db, cand, settings),
    )


def _cook_one_cap(db: Session, cand: Candidate, settings: dict[str, Any]) -> dict[str, Any]:
    today = _today_ist()
    cap = evaluate_publish_cap(
        published_today=seo_repo.count_published_on_day(db, today),
        soft_max_per_day=settings.get("soft_max_per_day"),
    )
    return pick(
        not cap.allow,
        lambda: {"skipped": True, "reason": "soft_max"},
        lambda: _cook_one_attempted(db, cand),
    )


def _cook_one_attempted(db: Session, cand: Candidate) -> dict[str, Any]:
    return pick(
        seo_repo.attempt_exists(db, cand.source_kind, cand.source_key),
        lambda: {"skipped": True, "reason": "already_attempted"},
        lambda: _cook_one_usefulness(db, cand),
    )


def _cook_one_usefulness(db: Session, cand: Candidate) -> dict[str, Any]:
    usefulness = evaluate_usefulness(cand.text, filename=cand.filename)
    return pick(
        not usefulness.useful,
        lambda: _skip(db, cand, f"triage:{usefulness.reason}"),
        lambda: _cook_one_write(db, cand),
    )


def _cook_one_write(db: Session, cand: Candidate) -> dict[str, Any]:
    scrubbed = scrub_pii(cand.text)
    article = write_article(
        db,
        source_text=scrubbed,
        stream=cand.stream,
        title_hint=cand.title_hint,
        angle_prompt=cand.angle_prompt,
        format_override=cand.format_override,
    )
    return pick(
        not article,
        lambda: _skip(db, cand, "write_failed"),
        lambda: _cook_one_dedupe(db, cand, article),
    )


def _cook_one_dedupe(db: Session, cand: Candidate, article: dict[str, Any]) -> dict[str, Any]:
    fp = topic_fingerprint(
        cand.stream,
        article.get("topic_fingerprint_hint") or article["title"],
    )
    fp = plan_seo_fingerprint(
        source_kind=cand.source_kind,
        source_key=cand.source_key,
        default_fingerprint=fp,
    )
    dedupe = evaluate_dedupe(
        db,
        fingerprint=fp,
        title=article["title"],
        lede=article["lede"],
    )
    return pick(
        not dedupe.ok,
        lambda: _skip(db, cand, f"dedupe:{dedupe.reason}"),
        lambda: _cook_one_publish(db, cand, article, fp),
    )


def _cook_one_publish(
    db: Session, cand: Candidate, article: dict[str, Any], fp: str
) -> dict[str, Any]:
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
    pick(
        cand.source_kind == "topic_queue" and bool((cand.source_ref or {}).get("queue_id")),
        lambda: _mark_topic_used(db, cand),
        lambda: None,
    )
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


def _mark_topic_used(db: Session, cand: Candidate) -> None:
    try:
        seo_repo.mark_topic_used(db, uuid.UUID(str(cand.source_ref["queue_id"])))
    except (ValueError, TypeError):
        pass


def cook_batch(db: Session, *, limit: int | None = None) -> dict[str, Any]:
    settings = seo_repo.get_settings(db)
    return pick(
        not evaluate_seo_cook_enabled(settings),
        lambda: {"ok": True, "skipped": "cook_disabled", "results": []},
        lambda: _cook_batch_cap(db, settings, limit),
    )


def _cook_batch_cap(
    db: Session, settings: dict[str, Any], limit: int | None
) -> dict[str, Any]:
    today = _today_ist()
    cap = evaluate_publish_cap(
        published_today=seo_repo.count_published_on_day(db, today),
        soft_max_per_day=settings.get("soft_max_per_day"),
    )
    return pick(
        not cap.allow,
        lambda: {"ok": True, "skipped": "soft_max", "results": []},
        lambda: _cook_batch_run(db, settings, today, cap.remaining, limit),
    )


def _cook_batch_run(
    db: Session,
    settings: dict[str, Any],
    today: date,
    remaining: int,
    limit: int | None,
) -> dict[str, Any]:
    tick = pick(limit is None, lambda: SEO_COOK_TICK_DEFAULT, lambda: int(limit))
    n = plan_seo_cook_tick(limit=tick, remaining=remaining)
    results: list[dict[str, Any]] = []
    cands = list(collect_candidates(db, batch_size=n))
    _cook_batch_loop(db, settings, today, cands, results)
    return {"ok": True, "results": results}


def _cook_batch_loop(
    db: Session,
    settings: dict[str, Any],
    today: date,
    cands: list[Candidate],
    results: list[dict[str, Any]],
) -> None:
    pick(
        not cands,
        lambda: None,
        lambda: _cook_batch_one(db, settings, today, cands, results),
    )


def _cook_batch_one(
    db: Session,
    settings: dict[str, Any],
    today: date,
    cands: list[Candidate],
    results: list[dict[str, Any]],
) -> None:
    cand = cands.pop(0)
    again = evaluate_publish_cap(
        published_today=seo_repo.count_published_on_day(db, today),
        soft_max_per_day=settings.get("soft_max_per_day"),
    )
    pick(
        not again.allow,
        lambda: cands.clear(),
        lambda: results.append(cook_one(db, cand))
        or _cook_batch_loop(db, settings, today, cands, results),
    )


def ensure_sd_daily(db: Session) -> dict[str, Any]:
    """Every IST calendar day ≥1 published system_design post."""
    settings = seo_repo.get_settings(db)
    today = _today_ist()
    cap = evaluate_publish_cap(
        published_today=seo_repo.count_published_on_day(db, today),
        soft_max_per_day=settings.get("soft_max_per_day"),
    )
    plan = plan_sd_daily_cook(
        cook_enabled=evaluate_seo_cook_enabled(settings),
        sd_published_today=seo_repo.count_sd_published_on_day(db, today),
        cap_allow=cap.allow,
    )
    return pick(
        not plan.cook,
        lambda: {"ok": True, "skipped": plan.reason},
        lambda: _sd_daily_try_sources(db, list(plan.source_order)),
    )


def _sd_daily_try_sources(db: Session, sources: list[str]) -> dict[str, Any]:
    return pick(
        not sources,
        lambda: {"ok": False, "skipped": "no_sd_candidates"},
        lambda: _sd_daily_try_one(db, sources),
    )


def _sd_daily_try_one(db: Session, sources: list[str]) -> dict[str, Any]:
    source = sources.pop(0)
    return apply(
        source,
        {
            "problem": lambda: _try_sd_problem(db, sources),
            "topic": lambda: _try_sd_topic(db, sources),
        },
    )


def _try_sd_problem(db: Session, sources: list[str]) -> dict[str, Any]:
    row = seo_repo.pick_unused_sd_problem(db)
    return pick(
        bool(row),
        lambda: cook_one(db, candidate_from_sd_problem(row)),
        lambda: _sd_daily_try_sources(db, sources),
    )


def _try_sd_topic(db: Session, sources: list[str]) -> dict[str, Any]:
    topic = seo_repo.pick_topic_queue(db, stream="system_design")
    return pick(
        bool(topic),
        lambda: cook_one(db, candidate_from_topic(topic)),
        lambda: _sd_daily_try_sources(db, sources),
    )


def _edition_source_key(paper_slug: str, edition_date: date) -> str:
    return f"{paper_slug}:{edition_date.isoformat()}"


def _edition_fingerprint(paper_slug: str, edition_date: date) -> str:
    return f"newspaper:{paper_slug}:{edition_date.isoformat()}"


def cook_edition_digest(
    db: Session, edition_id: uuid.UUID, *, force: bool = False
) -> dict[str, Any]:
    """Cook one edition digest when the edition is ready."""
    from app.repositories import newspaper as newspaper_repo

    settings = seo_repo.get_settings(db)
    return pick(
        not evaluate_seo_cook_enabled(settings),
        lambda: {"skipped": True, "reason": "cook_disabled"},
        lambda: _cook_edition_digest_enabled(db, edition_id, force, newspaper_repo),
    )


def _cook_edition_digest_enabled(
    db: Session, edition_id: uuid.UUID, force: bool, newspaper_repo: Any
) -> dict[str, Any]:
    ed = newspaper_repo.get_edition(db, edition_id)
    action = evaluate_edition_digest_preflight(ed, force=force)
    return apply(
        action,
        {
            "edition_not_found": lambda: {"skipped": True, "reason": "edition_not_found"},
            "edition_not_ready": lambda: {"skipped": True, "reason": "edition_not_ready"},
            "already_published": lambda: {"skipped": True, "reason": "already_published"},
            "ok": lambda: _cook_digest_body(db, edition_id, ed, force, newspaper_repo),
        },
    )


def _cook_digest_body(
    db: Session,
    edition_id: uuid.UUID,
    ed: dict[str, Any],
    force: bool,
    newspaper_repo: Any,
) -> dict[str, Any]:
    paper_slug = str(ed["paper_slug"])
    edition_date = ed["edition_date"]
    source_key = _edition_source_key(paper_slug, edition_date)
    fp = _edition_fingerprint(paper_slug, edition_date)
    existing_post_id = pick(
        bool(ed.get("blog_post_id")),
        lambda: uuid.UUID(str(ed["blog_post_id"])),
        lambda: None,
    )
    return pick(
        force,
        lambda: _digest_from_doc(
            db,
            edition_id,
            ed,
            force,
            newspaper_repo,
            paper_slug,
            edition_date,
            source_key,
            fp,
            existing_post_id,
        ),
        lambda: _digest_noforce(
            db,
            edition_id,
            ed,
            newspaper_repo,
            paper_slug,
            edition_date,
            source_key,
            fp,
            existing_post_id,
        ),
    )


def _digest_noforce(
    db: Session,
    edition_id: uuid.UUID,
    ed: dict[str, Any],
    newspaper_repo: Any,
    paper_slug: str,
    edition_date: date,
    source_key: str,
    fp: str,
    existing_post_id: uuid.UUID | None,
) -> dict[str, Any]:
    linked = _sync_edition_blog_link(db, edition_id=edition_id, fingerprint=fp)
    return pick(
        bool(linked),
        lambda: linked,
        lambda: _digest_after_link_check(
            db,
            edition_id,
            ed,
            newspaper_repo,
            paper_slug,
            edition_date,
            source_key,
            fp,
            existing_post_id,
        ),
    )


def _digest_after_link_check(
    db: Session,
    edition_id: uuid.UUID,
    ed: dict[str, Any],
    newspaper_repo: Any,
    paper_slug: str,
    edition_date: date,
    source_key: str,
    fp: str,
    existing_post_id: uuid.UUID | None,
) -> dict[str, Any]:
    return pick(
        not seo_repo.edition_digest_may_enqueue(db, "newspaper_edition", source_key),
        lambda: {"skipped": True, "reason": "already_attempted"},
        lambda: _digest_reset_then_write(
            db,
            edition_id,
            ed,
            newspaper_repo,
            paper_slug,
            edition_date,
            source_key,
            fp,
            existing_post_id,
        ),
    )


def _digest_reset_then_write(
    db: Session,
    edition_id: uuid.UUID,
    ed: dict[str, Any],
    newspaper_repo: Any,
    paper_slug: str,
    edition_date: date,
    source_key: str,
    fp: str,
    existing_post_id: uuid.UUID | None,
) -> dict[str, Any]:
    newspaper_repo.reset_edition_blog_for_retry(db, edition_id)
    return _digest_from_doc(
        db,
        edition_id,
        ed,
        False,
        newspaper_repo,
        paper_slug,
        edition_date,
        source_key,
        fp,
        existing_post_id,
    )


def _digest_from_doc(
    db: Session,
    edition_id: uuid.UUID,
    ed: dict[str, Any],
    force: bool,
    newspaper_repo: Any,
    paper_slug: str,
    edition_date: date,
    source_key: str,
    fp: str,
    existing_post_id: uuid.UUID | None,
) -> dict[str, Any]:
    from app.services.newspaper import aggregate_worthy_newspaper_text

    doc_id = ed.get("document_id")
    return pick(
        not doc_id,
        lambda: _skip_edition(db, edition_id, source_key, "no_document"),
        lambda: _digest_source(
            db,
            edition_id,
            ed,
            force,
            newspaper_repo,
            paper_slug,
            edition_date,
            source_key,
            fp,
            existing_post_id,
            doc_id,
            aggregate_worthy_newspaper_text,
        ),
    )


def _digest_source(
    db: Session,
    edition_id: uuid.UUID,
    ed: dict[str, Any],
    force: bool,
    newspaper_repo: Any,
    paper_slug: str,
    edition_date: date,
    source_key: str,
    fp: str,
    existing_post_id: uuid.UUID | None,
    doc_id: Any,
    aggregate_worthy_newspaper_text: Any,
) -> dict[str, Any]:
    source_text = aggregate_worthy_newspaper_text(db, uuid.UUID(str(doc_id)))
    skip_reason = digest_skip_reason(evaluate_digest_source_ready(source_text))
    return pick(
        bool(skip_reason),
        lambda: _skip_edition(db, edition_id, source_key, skip_reason),
        lambda: _digest_write_article(
            db,
            edition_id,
            ed,
            force,
            newspaper_repo,
            paper_slug,
            edition_date,
            source_key,
            fp,
            existing_post_id,
            doc_id,
            source_text,
        ),
    )


def _digest_write_article(
    db: Session,
    edition_id: uuid.UUID,
    ed: dict[str, Any],
    force: bool,
    newspaper_repo: Any,
    paper_slug: str,
    edition_date: date,
    source_key: str,
    fp: str,
    existing_post_id: uuid.UUID | None,
    doc_id: Any,
    source_text: str,
) -> dict[str, Any]:
    scrubbed = scrub_pii(source_text)
    title_hint = f"{ed.get('paper_title') or paper_slug} · {edition_date.isoformat()}"
    article = write_edition_digest(
        db,
        source_text=scrubbed,
        edition_date=edition_date.isoformat(),
        title_hint=title_hint,
    )
    return pick(
        not article,
        lambda: _skip_edition(db, edition_id, source_key, "write_failed"),
        lambda: _digest_after_article(
            db,
            edition_id,
            force,
            newspaper_repo,
            paper_slug,
            edition_date,
            source_key,
            fp,
            existing_post_id,
            doc_id,
            article,
        ),
    )


def _digest_after_article(
    db: Session,
    edition_id: uuid.UUID,
    force: bool,
    newspaper_repo: Any,
    paper_slug: str,
    edition_date: date,
    source_key: str,
    fp: str,
    existing_post_id: uuid.UUID | None,
    doc_id: Any,
    article: dict[str, Any],
) -> dict[str, Any]:
    fp = _edition_fingerprint(paper_slug, edition_date)
    dedupe = evaluate_dedupe(
        db,
        fingerprint=fp,
        title=article["title"],
        lede=article["lede"],
    )
    bypass = should_bypass_digest_dedupe(force=force, existing_post_id=existing_post_id)
    return pick(
        not dedupe.ok and not bypass,
        lambda: _digest_dedupe_fail(
            db, edition_id, source_key, fp, dedupe.reason
        ),
        lambda: _digest_persist(
            db,
            edition_id,
            force,
            newspaper_repo,
            paper_slug,
            edition_date,
            source_key,
            fp,
            existing_post_id,
            doc_id,
            article,
            bypass,
        ),
    )


def _digest_dedupe_fail(
    db: Session,
    edition_id: uuid.UUID,
    source_key: str,
    fp: str,
    reason: str,
) -> dict[str, Any]:
    return pick(
        reason == "fingerprint_taken",
        lambda: _digest_link_or_skip(db, edition_id, source_key, fp, reason),
        lambda: _skip_edition(db, edition_id, source_key, f"dedupe:{reason}"),
    )


def _digest_link_or_skip(
    db: Session,
    edition_id: uuid.UUID,
    source_key: str,
    fp: str,
    reason: str,
) -> dict[str, Any]:
    linked = _link_existing_edition_post(
        db,
        edition_id=edition_id,
        fingerprint=fp,
        source_key=source_key,
    )
    return pick(
        bool(linked),
        lambda: linked,
        lambda: _skip_edition(db, edition_id, source_key, f"dedupe:{reason}"),
    )


def _digest_persist(
    db: Session,
    edition_id: uuid.UUID,
    force: bool,
    newspaper_repo: Any,
    paper_slug: str,
    edition_date: date,
    source_key: str,
    fp: str,
    existing_post_id: uuid.UUID | None,
    doc_id: Any,
    article: dict[str, Any],
    bypass: bool,
) -> dict[str, Any]:
    embedding = embed_title_lede(article["title"], article["lede"])
    post_id, slug = pick(
        bypass,
        lambda: _digest_update_existing(
            db, paper_slug, edition_date, existing_post_id, article, embedding
        ),
        lambda: _digest_insert_new(
            db,
            edition_id,
            newspaper_repo,
            paper_slug,
            edition_date,
            fp,
            article,
            embedding,
        ),
    )
    _digest_attach_mcqs(db, post_id, slug, doc_id, article)
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
        reason=choose(force, "recooked", "ok"),
        post_id=post_id,
    )
    db.commit()
    logger.info(
        "edition digest %s edition=%s slug=%s",
        choose(force, "recooked", "published"),
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


def _digest_update_existing(
    db: Session,
    paper_slug: str,
    edition_date: date,
    existing_post_id: uuid.UUID | None,
    article: dict[str, Any],
    embedding: Any,
) -> tuple[uuid.UUID, str]:
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
    return post_id, slug


def _digest_insert_new(
    db: Session,
    edition_id: uuid.UUID,
    newspaper_repo: Any,
    paper_slug: str,
    edition_date: date,
    fp: str,
    article: dict[str, Any],
    embedding: Any,
) -> tuple[uuid.UUID, str]:
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
    return post_id, slug


def _digest_attach_mcqs(
    db: Session,
    post_id: uuid.UUID,
    slug: str,
    doc_id: Any,
    article: dict[str, Any],
) -> None:
    try:
        attached = attach_edition_mcqs(
            db,
            post_id=post_id,
            document_id=uuid.UUID(str(doc_id)),
        )
        pick(
            not evaluate_seo_mcq_attach_ready(len(attached)),
            lambda: attach_or_generate_mcqs(
                db,
                post_id=post_id,
                slug=slug,
                body_md=article["body_md"],
                source_document_id=uuid.UUID(str(doc_id)),
                target_count=plan_seo_mcq_attach_defaults().target_count,
            ),
            lambda: None,
        )
    except Exception:
        logger.exception("edition digest mcq attach failed post=%s", post_id)


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
    return pick(
        not existing,
        lambda: None,
        lambda: _link_existing_body(
            db, edition_id, source_key, existing, newspaper_repo
        ),
    )


def _link_existing_body(
    db: Session,
    edition_id: uuid.UUID,
    source_key: str,
    existing: dict[str, Any],
    newspaper_repo: Any,
) -> dict[str, Any]:
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
    post_id = (ed or {}).get("blog_post_id")
    post = pick(
        bool(ed) and bool(post_id),
        lambda: seo_repo.get_post_by_id(
            db, uuid.UUID(str(post_id)), include_internal=True
        ),
        lambda: None,
    )
    existing = pick(
        bool(ed),
        lambda: seo_repo.get_published_post_by_fingerprint(db, fingerprint),
        lambda: None,
    )
    action = evaluate_edition_blog_link(
        has_edition=bool(ed),
        has_post_id=bool(post_id),
        post_published=bool(post) and post.get("status") == "published",
        fingerprint_published=bool(existing),
    )
    return apply(
        action,
        {
            "missing": lambda: None,
            "already_linked": lambda: _already_linked_repair(
                db, edition_id, ed, post_id, newspaper_repo
            ),
            "link_existing": lambda: _link_existing_edition_post(
                db,
                edition_id=edition_id,
                fingerprint=fingerprint,
                source_key=_edition_source_key(
                    str(ed["paper_slug"]), ed["edition_date"]
                ),
            ),
            "continue": lambda: None,
        },
    )


def _already_linked_repair(
    db: Session,
    edition_id: uuid.UUID,
    ed: dict[str, Any],
    post_id: Any,
    newspaper_repo: Any,
) -> dict[str, Any]:
    pick(
        ed.get("blog_status") != "published",
        lambda: _relink_published(db, edition_id, post_id, newspaper_repo),
        lambda: None,
    )
    return {"skipped": True, "reason": "already_linked"}


def _relink_published(
    db: Session, edition_id: uuid.UUID, post_id: Any, newspaper_repo: Any
) -> None:
    newspaper_repo.link_edition_blog(
        db,
        edition_id=edition_id,
        post_id=uuid.UUID(str(post_id)),
        status="published",
    )
    db.commit()


def _skip_edition(
    db: Session,
    edition_id: uuid.UUID,
    source_key: str,
    reason: str,
) -> dict[str, Any]:
    from app.repositories import newspaper as newspaper_repo

    ed = newspaper_repo.get_edition(db, edition_id)
    post = pick(
        bool(ed) and bool(ed.get("blog_post_id")),
        lambda: seo_repo.get_post_by_id(
            db, uuid.UUID(str(ed["blog_post_id"])), include_internal=True
        ),
        lambda: None,
    )
    action = evaluate_edition_skip_link(
        has_post_id=bool(ed) and bool(ed.get("blog_post_id")),
        post_published=bool(post) and post.get("status") == "published",
    )
    return apply(
        action,
        {
            "keep_published": lambda: _skip_keep_published(
                db, edition_id, source_key, reason, ed, newspaper_repo
            ),
            "record_skip": lambda: _record_edition_skip(
                db, edition_id, source_key, reason, ed, newspaper_repo
            ),
        },
    )


def _skip_keep_published(
    db: Session,
    edition_id: uuid.UUID,
    source_key: str,
    reason: str,
    ed: dict[str, Any],
    newspaper_repo: Any,
) -> dict[str, Any]:
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


def _record_edition_skip(
    db: Session,
    edition_id: uuid.UUID,
    source_key: str,
    reason: str,
    ed: dict[str, Any] | None,
    newspaper_repo: Any,
) -> dict[str, Any]:
    seo_repo.record_attempt(
        db,
        source_kind="newspaper_edition",
        source_key=source_key,
        outcome="skipped",
        reason=reason,
    )
    blog_status = evaluate_edition_digest_skip_status(reason)
    pick(
        not (ed and ed.get("blog_post_id")),
        lambda: newspaper_repo.set_edition_blog_status(
            db, edition_id, status=blog_status
        ),
        lambda: None,
    )
    db.commit()
    logger.info("edition digest skip %s: %s", source_key, reason)
    return {"skipped": True, "reason": reason, "source_key": source_key}
