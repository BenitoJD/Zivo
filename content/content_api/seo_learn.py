"""Public /learn blog API + admin kill-switch / unpublish.

Mounted at /api/learn — separate from /api/artifacts learn-queue.
Public responses never include source_kind / source_ref.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.engine_runtime import apply, pick
from app.repositories import newspaper as newspaper_repo
from app.repositories import seo as seo_repo
from app.services.auth import require_admin, require_csrf
from app.services.presence import evaluate_presence

router = APIRouter()


class PostStatusIn(BaseModel):
    status: str = Field(..., pattern="^(published|unpublished)$")


class SettingsIn(BaseModel):
    cook_enabled: bool | None = None
    soft_max_per_day: int | None = Field(default=None, ge=1, le=100)


def _raise_http(status: int, detail: str) -> None:
    raise HTTPException(status_code=status, detail=detail)


def _require_present(value, detail: str) -> None:
    apply(
        evaluate_presence(value).action,
        {
            "missing": lambda: _raise_http(404, detail),
            "empty": lambda: _raise_http(404, detail),
            "ok": lambda: None,
        },
    )


def _iso(value):
    return pick(bool(value), lambda: value.isoformat(), lambda: None)


def _strip_internal(post: dict) -> dict:
    post.pop("source_kind", None)
    post.pop("source_ref", None)
    post.pop("topic_fingerprint", None)
    post.pop("artifact_id", None)
    return post


def _strip_answers(out: dict) -> dict:
    for item in out["items"]:
        item.pop("correct_index", None)
        item.pop("correct_indices", None)
        item.pop("explanation", None)
    return out


@router.get("/posts")
def list_posts(
    stream: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    pick(
        stream is not None and stream not in {"general", "system_design"},
        lambda: _raise_http(400, "Invalid stream"),
        lambda: None,
    )
    return seo_repo.list_published(db, stream=stream, limit=limit, offset=offset)


@router.get("/posts/{slug}")
def get_post(slug: str, db: Session = Depends(get_db)) -> dict:
    post = seo_repo.get_post_by_slug(db, slug, published_only=True)
    _require_present(post, "Post not found")
    return _strip_internal(post)


@router.get("/posts/{slug}/questions")
def post_questions(
    slug: str,
    limit: int = Query(default=20, ge=1, le=40),
    db: Session = Depends(get_db),
) -> dict:
    out = seo_repo.list_post_questions(db, slug, limit=limit)
    pick(out["post"] is None, lambda: _raise_http(404, "Post not found"), lambda: None)
    return _strip_answers(out)


@router.get("/sitemap-slugs")
def sitemap_slugs(db: Session = Depends(get_db)) -> dict:
    rows = seo_repo.list_sitemap_slugs(db)
    newspaper_rows = seo_repo.list_newspaper_blog_sitemap(db)
    return {
        "items": [
            {
                "slug": r["slug"],
                "published_at": _iso(r.get("published_at")),
                "updated_at": _iso(r.get("updated_at")),
            }
            for r in rows
        ],
        "newspaper": [
            {
                "paper_slug": r["paper_slug"],
                "edition_date": r["edition_date"].isoformat(),
                "published_at": _iso(r.get("blog_published_at")),
                "updated_at": _iso(r.get("updated_at")),
            }
            for r in newspaper_rows
        ],
    }


@router.get("/newspaper/{paper_slug}")
def newspaper_blog_archive(paper_slug: str, db: Session = Depends(get_db)) -> dict:
    pick(
        newspaper_repo.is_brand_allowed(db, paper_slug),
        lambda: None,
        lambda: _raise_http(404, "Paper not found"),
    )
    items = seo_repo.list_edition_blog_archive(db, paper_slug=paper_slug)
    row = db.execute(
        text(
            """
            SELECT paper_title FROM qb.newspaper_edition
            WHERE paper_slug = :slug
            ORDER BY edition_date DESC
            LIMIT 1
            """
        ),
        {"slug": paper_slug},
    ).first()
    title = pick(bool(row) and bool(row[0]), lambda: str(row[0]), lambda: paper_slug)
    return {
        "paper_slug": paper_slug,
        "paper_title": title,
        "items": items,
    }


@router.get("/newspaper/{paper_slug}/{edition_date}")
def get_newspaper_edition_blog(
    paper_slug: str,
    edition_date: date,
    db: Session = Depends(get_db),
) -> dict:
    pick(
        newspaper_repo.is_brand_allowed(db, paper_slug),
        lambda: None,
        lambda: _raise_http(404, "Edition not found"),
    )
    post = seo_repo.get_edition_blog_post(
        db, paper_slug=paper_slug, edition_date=edition_date
    )
    _require_present(post, "Edition not found")
    return _strip_internal(post)


@router.get("/newspaper/{paper_slug}/{edition_date}/questions")
def newspaper_edition_questions(
    paper_slug: str,
    edition_date: date,
    limit: int = Query(default=20, ge=1, le=40),
    db: Session = Depends(get_db),
) -> dict:
    post = seo_repo.get_edition_blog_post(
        db, paper_slug=paper_slug, edition_date=edition_date
    )
    _require_present(post, "Edition not found")
    out = seo_repo.list_post_questions(db, post["slug"], limit=limit)
    pick(out["post"] is None, lambda: _raise_http(404, "Edition not found"), lambda: None)
    return _strip_answers(out)


@router.get("/admin/settings", dependencies=[Depends(require_admin)])
def get_settings(db: Session = Depends(get_db)) -> dict:
    s = seo_repo.get_settings(db)
    return {
        "cook_enabled": bool(s.get("cook_enabled")),
        "soft_max_per_day": int(s.get("soft_max_per_day") or 20),
        "updated_at": _iso(s.get("updated_at")),
    }


@router.patch("/admin/settings", dependencies=[Depends(require_admin), Depends(require_csrf)])
def patch_settings(body: SettingsIn, db: Session = Depends(get_db)) -> dict:
    s = seo_repo.update_settings(
        db,
        cook_enabled=body.cook_enabled,
        soft_max_per_day=body.soft_max_per_day,
    )
    return {
        "cook_enabled": bool(s.get("cook_enabled")),
        "soft_max_per_day": int(s.get("soft_max_per_day") or 20),
        "updated_at": _iso(s.get("updated_at")),
    }


@router.get("/admin/posts", dependencies=[Depends(require_admin)])
def admin_list_posts(
    limit: int = Query(default=40, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    return {"items": seo_repo.list_admin_posts(db, limit=limit)}


@router.patch(
    "/admin/posts/{post_id}",
    dependencies=[Depends(require_admin), Depends(require_csrf)],
)
def admin_patch_post(
    post_id: uuid.UUID,
    body: PostStatusIn,
    db: Session = Depends(get_db),
) -> dict:
    post = seo_repo.set_post_status(db, post_id, body.status)
    _require_present(post, "Post not found")
    return post
