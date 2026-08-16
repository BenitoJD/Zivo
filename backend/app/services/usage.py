"""Message usage limits — logged-in daily + demo cookie/IP."""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException, Request, Response
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.engine_runtime import pick
from app.models import Account, DemoUsage, UsageDaily
from app.services.request_ip import client_ip

settings = get_settings()
DEMO_COOKIE = "zivo_demo_id"


def _today() -> datetime:
    d = date.today()
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _ip_hash(request: Request) -> str:
    return hashlib.sha256(client_ip(request).encode()).hexdigest()


def _limit() -> None:
    raise HTTPException(status_code=429, detail="Daily message limit reached")


def _guest_limit() -> None:
    raise HTTPException(
        status_code=429,
        detail=f"Message limit reached ({settings.guest_message_limit}) — sign in to continue",
    )


def ensure_demo_cookie(response: Response | None, cookie_id: str | None) -> str:
    def _new() -> str:
        new_id = uuid.uuid4().hex
        pick(response is not None, lambda: _set_cookie(response, new_id), lambda: None)
        return new_id

    return pick(bool(cookie_id), lambda: cookie_id, _new)


def _set_cookie(response: Response, new_id: str) -> None:
    from app.services.auth import cookie_set_kwargs

    response.set_cookie(
        key=DEMO_COOKIE,
        value=new_id,
        **cookie_set_kwargs(max_age=86400 * 30),
    )


def _guest_usage_total(db: Session, *, ip_h: str, cookie: str, today: datetime) -> int:
    total = (
        db.query(func.coalesce(func.sum(DemoUsage.message_count), 0))
        .filter(
            DemoUsage.usage_date == today,
            or_(DemoUsage.ip_hash == ip_h, DemoUsage.cookie_id == cookie),
        )
        .scalar()
    )
    return int(total or 0)


def check_message_allowed(
    db: Session,
    *,
    user: Account | None,
    request: Request,
    demo_cookie: str | None,
) -> None:
    today = _today()

    def _user() -> None:
        row = (
            db.query(UsageDaily)
            .filter(UsageDaily.account_id == user.id, UsageDaily.usage_date == today)
            .first()
        )
        count = pick(bool(row), lambda: row.message_count, lambda: 0)
        pick(count >= settings.daily_message_limit, _limit, lambda: None)

    def _guest() -> None:
        ip_h = _ip_hash(request)
        cookie = demo_cookie or ""
        pick(
            _guest_usage_total(db, ip_h=ip_h, cookie=cookie, today=today) >= settings.guest_message_limit,
            _guest_limit,
            lambda: None,
        )

    pick(bool(user), _user, _guest)


def _atomic_increment_daily(db: Session, account_id: uuid.UUID, today: datetime) -> int:
    row = db.execute(
        text(
            """
            UPDATE usage_daily
            SET message_count = message_count + 1
            WHERE account_id = :account_id
              AND usage_date = :usage_date
              AND message_count < :limit
            RETURNING message_count
            """
        ),
        {
            "account_id": account_id,
            "usage_date": today,
            "limit": settings.daily_message_limit,
        },
    ).first()

    def _insert() -> int:
        inserted = db.execute(
            text(
                """
                INSERT INTO usage_daily (id, account_id, usage_date, message_count)
                VALUES (gen_random_uuid(), :account_id, :usage_date, 1)
                ON CONFLICT (account_id, usage_date) DO NOTHING
                RETURNING message_count
                """
            ),
            {"account_id": account_id, "usage_date": today},
        ).first()

        def _retry() -> int:
            row2 = db.execute(
                text(
                    """
                    UPDATE usage_daily
                    SET message_count = message_count + 1
                    WHERE account_id = :account_id
                      AND usage_date = :usage_date
                      AND message_count < :limit
                    RETURNING message_count
                    """
                ),
                {
                    "account_id": account_id,
                    "usage_date": today,
                    "limit": settings.daily_message_limit,
                },
            ).first()
            pick(not row2, _limit, lambda: None)
            return int(row2[0])

        return pick(bool(inserted), lambda: int(inserted[0]), _retry)

    return pick(bool(row), lambda: int(row[0]), _insert)


def _atomic_increment_demo(
    db: Session,
    *,
    ip_h: str,
    cookie: str,
    today: datetime,
) -> None:
    pick(
        _guest_usage_total(db, ip_h=ip_h, cookie=cookie, today=today) >= settings.guest_message_limit,
        _guest_limit,
        lambda: None,
    )

    row = db.execute(
        text(
            """
            INSERT INTO demo_usage (id, ip_hash, cookie_id, usage_date, message_count)
            VALUES (gen_random_uuid(), :ip_hash, :cookie_id, :usage_date, 1)
            ON CONFLICT (ip_hash, cookie_id, usage_date)
            DO UPDATE SET message_count = demo_usage.message_count + 1
            RETURNING message_count
            """
        ),
        {"ip_hash": ip_h, "cookie_id": cookie, "usage_date": today},
    ).first()
    pick(not row, _guest_limit, lambda: None)

    def _over() -> None:
        db.execute(
            text(
                """
                UPDATE demo_usage
                SET message_count = message_count - 1
                WHERE ip_hash = :ip_hash
                  AND cookie_id = :cookie_id
                  AND usage_date = :usage_date
                  AND message_count > 0
                """
            ),
            {"ip_hash": ip_h, "cookie_id": cookie, "usage_date": today},
        )
        _guest_limit()

    pick(
        _guest_usage_total(db, ip_h=ip_h, cookie=cookie, today=today) > settings.guest_message_limit,
        _over,
        lambda: None,
    )


def reserve_message_slot(
    db: Session,
    *,
    user: Account | None,
    request: Request,
    demo_cookie: str | None,
) -> None:
    """Atomically reserve one message slot before LLM dispatch."""
    today = _today()

    def _user() -> None:
        count = _atomic_increment_daily(db, user.id, today)

        def _rollback() -> None:
            db.execute(
                text(
                    """
                    UPDATE usage_daily
                    SET message_count = message_count - 1
                    WHERE account_id = :account_id
                      AND usage_date = :usage_date
                      AND message_count > 0
                    """
                ),
                {"account_id": user.id, "usage_date": today},
            )
            _limit()

        pick(count > settings.daily_message_limit, _rollback, lambda: None)
        db.commit()

    def _guest() -> None:
        ip_h = _ip_hash(request)
        cookie = demo_cookie or ""
        _atomic_increment_demo(db, ip_h=ip_h, cookie=cookie, today=today)
        db.commit()

    pick(bool(user), _user, _guest)


def increment_message_count(
    db: Session,
    *,
    user: Account | None,
    request: Request,
    demo_cookie: str | None,
) -> None:
    """Legacy post-dispatch increment — no-op when slot already reserved."""
    return
