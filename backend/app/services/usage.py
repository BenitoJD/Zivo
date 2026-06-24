"""Message usage limits — logged-in daily + demo cookie/IP."""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException, Request, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Account, DemoUsage, UsageDaily

settings = get_settings()
DEMO_COOKIE = "zivo_demo_id"


def _today() -> datetime:
    d = date.today()
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _ip_hash(request: Request) -> str:
    """Stable per-caller IP hash.

    Trust order: `request.client.host` (set by uvicorn / ProxyHeadersMiddleware
    to the connecting peer or the proxy-stripped value) → request.client.host
    when no `X-Forwarded-For` is set. The header is **not** trusted from the
    client side without a configured trusted-proxy allowlist, so we deliberately
    do NOT pull from `X-Forwarded-For` here. The rate limit is best-effort
    guidance, not a security boundary.
    """
    ip = request.client.host if request.client else "unknown"
    return hashlib.sha256(ip.encode()).hexdigest()


def ensure_demo_cookie(response: Response | None, cookie_id: str | None) -> str:
    if cookie_id:
        return cookie_id
    new_id = uuid.uuid4().hex
    if response is not None:
        response.set_cookie(
            key=DEMO_COOKIE,
            value=new_id,
            httponly=True,
            secure=settings.is_production,
            samesite="lax",
            max_age=86400 * 30,
            path="/",
        )
    return new_id


def check_message_allowed(
    db: Session,
    *,
    user: Account | None,
    request: Request,
    demo_cookie: str | None,
) -> None:
    today = _today()
    if user:
        row = (
            db.query(UsageDaily)
            .filter(UsageDaily.account_id == user.id, UsageDaily.usage_date == today)
            .first()
        )
        count = row.message_count if row else 0
        if count >= settings.daily_message_limit:
            raise HTTPException(status_code=429, detail="Daily message limit reached")
        return

    ip_h = _ip_hash(request)
    cookie = demo_cookie or ""
    total = (
        db.query(func.coalesce(func.sum(DemoUsage.message_count), 0))
        .filter(DemoUsage.ip_hash == ip_h, DemoUsage.cookie_id == cookie)
        .scalar()
    )
    if int(total or 0) >= settings.guest_message_limit:
        raise HTTPException(
            status_code=429,
            detail=f"Message limit reached ({settings.guest_message_limit}) — sign in to continue",
        )


def increment_message_count(
    db: Session,
    *,
    user: Account | None,
    request: Request,
    demo_cookie: str | None,
) -> None:
    today = _today()
    if user:
        row = (
            db.query(UsageDaily)
            .filter(UsageDaily.account_id == user.id, UsageDaily.usage_date == today)
            .first()
        )
        if row:
            row.message_count += 1
        else:
            db.add(UsageDaily(account_id=user.id, usage_date=today, message_count=1))
        db.commit()
        return

    ip_h = _ip_hash(request)
    cookie = demo_cookie or ""
    row = (
        db.query(DemoUsage)
        .filter(
            DemoUsage.ip_hash == ip_h,
            DemoUsage.cookie_id == cookie,
            DemoUsage.usage_date == today,
        )
        .first()
    )
    if row:
        row.message_count += 1
    else:
        db.add(DemoUsage(ip_hash=ip_h, cookie_id=cookie, usage_date=today, message_count=1))
    db.commit()
